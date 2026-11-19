"""Explicit display pixels for a local point-grounding VLM, normalized motor targets on return."""

import base64
from io import BytesIO
from math import isqrt

from PIL import Image, ImageOps


def pixel_request(data, max_pixels):
    schema = data["schema"]
    if not {"primitive", "view", "x", "y"} <= schema.get("properties", {}).keys():
        return data, {}
    side = (isqrt(max_pixels) // 28) * 28
    if side < 56:
        raise ValueError(
            "The local visual model needs a canvas of at least 56 pixels per side"
        )
    required = {"front", "back", "left", "right"}
    if "inspection" in schema["properties"]["view"].get("enum", []):
        required.add("inspection")
    views = {}
    images = []
    view = None
    for part in data["images"]:
        if part["type"] == "input_text":
            tag = part["text"].removeprefix("Current RGB ")
            view = (
                tag
                if part["text"].startswith("Current RGB ") and tag in required
                else None
            )
            images.append(part)
            continue
        if not view:
            images.append(part)
            continue
        raw = base64.b64decode(part["image_url"].split(",", 1)[1], validate=True)
        with Image.open(BytesIO(raw)) as original:
            fitted = ImageOps.contain(
                original.convert("RGB"), (side, side), Image.Resampling.LANCZOS
            )
        x, y = (side - fitted.width) // 2, (side - fitted.height) // 2
        canvas = Image.new("RGB", (side, side))
        canvas.paste(fitted, (x, y))
        encoded = BytesIO()
        canvas.save(encoded, format="PNG")
        images.append(
            {
                **part,
                "image_url": "data:image/png;base64,"
                + base64.b64encode(encoded.getvalue()).decode("ascii"),
            }
        )
        views[view] = (x, y, fitted.width, fitted.height)
        view = None
    if set(views) != required:
        raise ValueError(
            "The point grounder needs every requested labelled current image"
        )
    fields = {k: v for k, v in schema["properties"].items() if k not in ("x", "y")}
    fields.update(
        {
            k: {"type": "integer", "minimum": 0, "maximum": side - 1}
            for k in ("x_pixel", "y_pixel")
        }
    )
    instructions = data["instructions"].replace(
        "a precise normalized [0,1] interior point",
        "an interior point in displayed pixels",
    )
    instructions += f"\nEach current image is {side} by {side} pixels. Return x_pixel from the left and y_pixel from the top, both integers from 0 to {side - 1}. Use the current full view named in view. Historical crops cannot supply action points."
    if "inspection" in required:
        instructions += " For a target in the live inspection crop select view=inspection and use its displayed pixel coordinates."
    return {
        **data,
        "instructions": instructions,
        "images": images,
        "schema": {**schema, "properties": fields, "required": list(fields)},
    }, views


def motor_decision(answer, views):
    if not views:
        return answer
    result = dict(answer)
    x, y = result.pop("x_pixel"), result.pop("y_pixel")
    if result["decision"] == "act" and result["primitive"] not in (
        "look",
        "wait",
        "release",
    ):
        left, top, width, height = views[result["view"]]
        if (
            type(x) is not int
            or type(y) is not int
            or not left <= x < left + width
            or not top <= y < top + height
        ):
            raise ValueError(
                "Choose a pixel on the visible scene, inside the image padding"
            )
        result.update(
            x=(x - left) / max(1, width - 1), y=(y - top) / max(1, height - 1)
        )
    else:
        result.update(x=0.0, y=0.0)
    return result
