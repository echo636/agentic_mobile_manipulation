"""Loopback inference for a separately provisioned Qwen2.5-VL model."""

import argparse
import base64
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from ..mcp_server import loopback_host
from .visual_coordinates import motor_decision, pixel_request


class LocalVisualRole:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}/decision"
        self.opener = build_opener(ProxyHandler({}))

    def ask(self, instructions, schema, context, images):
        data = json.dumps(
            {
                "instructions": instructions,
                "schema": schema,
                "context": context,
                "images": images,
            }
        ).encode()
        req = Request(self.url, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=600) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 422:
                raise ValueError(
                    "The visual grounder could not return a valid decision for the current views"
                ) from exc
            raise


class LocalInference:
    def __init__(self, path, pixels):
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        self.pixels = pixels
        self.processor = AutoProcessor.from_pretrained(
            path, local_files_only=True, min_pixels=4 * 28 * 28, max_pixels=pixels
        )
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            path,
            local_files_only=True,
            dtype=torch.bfloat16,
            device_map={"": "cuda:0"},
            attn_implementation="sdpa",
        ).eval()

    def __call__(self, data):
        import torch
        from jsonschema import ValidationError, validate
        from PIL import Image

        data, views = pixel_request(data, self.pixels)
        photos = []
        content = [
            {"type": "text", "text": json.dumps(data["context"], ensure_ascii=False)}
        ]
        for part in data["images"]:
            if part["type"] == "input_image":
                raw = base64.b64decode(
                    part["image_url"].split(",", 1)[1], validate=True
                )
                photos.append(Image.open(BytesIO(raw)).convert("RGB"))
                content.append({"type": "image"})
            else:
                content.append({"type": "text", "text": part["text"]})
        instruction = (
            data["instructions"]
            + "\nReturn exactly one JSON object matching this schema.\n"
            + json.dumps(data["schema"])
        )
        for attempt in range(2):
            msgs = [
                {"role": "system", "content": instruction},
                {"role": "user", "content": content},
            ]
            prompt = self.processor.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True
            )
            inputs = self.processor(
                text=[prompt], images=photos, return_tensors="pt"
            ).to(self.model.device)
            with torch.inference_mode():
                tokens = self.model.generate(
                    **inputs, max_new_tokens=768, do_sample=False
                )
            answer = self.processor.decode(
                tokens[0, len(inputs.input_ids[0]) :], skip_special_tokens=True
            )
            try:
                result = json.loads(
                    answer.strip().removeprefix("```json").removesuffix("```").strip()
                )
                validate(result, data["schema"])
                return motor_decision(result, views)
            except (ValueError, ValidationError) as exc:
                if attempt:
                    raise ValueError(
                        "Fast model returned invalid decision JSON twice"
                    ) from exc
                content.append(
                    {
                        "type": "text",
                        "text": "Correct the JSON format. "
                        + str(exc).split("\n")[0][:260],
                    }
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18467)
    parser.add_argument("--host", type=loopback_host, default="127.0.0.1")
    parser.add_argument("--pixels", type=int, default=512 * 512)
    args = parser.parse_args()
    infer = LocalInference(args.model_path, args.pixels)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/decision":
                return self.send_error(404)
            try:
                size = int(self.headers["Content-Length"])
                if not 0 < size <= 64 * 1024**2:
                    return self.send_error(413)
                answer = infer(json.loads(self.rfile.read(size)))
            except (ValueError, TypeError, KeyError, OSError) as exc:
                print(type(exc).__name__, str(exc)[:320], flush=True)
                return self.send_error(422)
            except RuntimeError as exc:
                print(type(exc).__name__, str(exc)[:320], flush=True)
                return self.send_error(503)
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print("Visual inference ready", flush=True)
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
