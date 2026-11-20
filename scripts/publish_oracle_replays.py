"""Publish selected, finalized Oracle viewing artifacts into a dedicated static root."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from build_oracle_video_review import build, build_index


def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def link_media(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        if source.samefile(destination):
            return
        if digest(source)!=digest(destination):
            raise FileExistsError("Published media is immutable: "+str(destination))
        return
    try:
        os.link(source,destination)
    except OSError:
        shutil.copy2(source,destination)


def publish(runs,root):
    root.mkdir(parents=True,exist_ok=True)
    cases=[]
    manifest=[]
    for run in runs:
        run=run.resolve()
        env=run/"env"
        recording=json.loads((env/"video.json").read_text())
        if recording.get("status")!="passed":
            raise ValueError("Only finalized recordings may be published")
        build(run)
        report=json.loads((run/"oracle_review.json").read_text())
        # The static root contains only human-facing reports and rendered media.
        report["config"]={key:report["config"][key] for key in
            ("task","instance","seed","instruction","agent_profile","grasping_mode","observation_mode","privileged_observation","ik_backend","wrist_cameras")
            if key in report["config"]}
        target=root/run.name
        target.mkdir(exist_ok=True)
        media=report["media"]
        files={entry["file"] for entry in media.values()
               if isinstance(entry,dict) and "file" in entry} | {media["poster"]}
        for name in files:
            link_media(run/name,target/name)
        encoded=json.dumps(report,ensure_ascii=False).replace("<","\\u003c")
        page=(run/"oracle_review.html").read_text()
        page,count=re.subn(r'(<script id="data" type="application/json">).*?(</script>)',
                          lambda m:m[1]+encoded+m[2],page,count=1,flags=re.S)
        if count!=1:raise ValueError("Unexpected report template")
        (target/"oracle_review.html").write_text(page)
        (target/"oracle_review.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
        manifest.append({"case":run.name,"source_run":str(run),
            "source_video_sha256":digest(env/"episode.mp4"),
            "preview":media["preview"],"full":media["full"],
            "source_recording":{key:recording.get(key) for key in
               ("frame_count","duration_seconds","synthesized_motion","every_env_step_recorded")},
            "published_media":[{"file":name,"bytes":(target/name).stat().st_size,"sha256":digest(target/name)} for name in sorted(files)]})
        cases.append(target)
    build_index(cases,root/"index.html")
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,action="append",required=True)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--manifest",type=Path,required=True,help="Private provenance output, outside public root")
    args=parser.parse_args()
    root=args.root.resolve()
    if args.manifest.resolve().is_relative_to(root):
        raise ValueError("Keep private publication provenance outside the static root")
    records=publish(args.run,root)
    args.manifest.write_text(json.dumps(records,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps([{"case":r["case"],"preview_bytes":r["preview"]["bytes"],
                      "full_bytes":r["full"]["bytes"]} for r in records]),flush=True)
