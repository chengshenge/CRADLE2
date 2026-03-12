import argparse, json, os, io, base64
from pathlib import Path
from datasets import load_dataset

try:
    from PIL import Image as PILImage
except Exception as e:
    PILImage = None


def build_prompt(ex):
    q = (ex.get("question") or "").strip()
    hint = (ex.get("hint") or "").strip()
    qtype = ex.get("question_type") or ""
    choices = ex.get("choices") or []

    parts = []
    if hint:
        parts.append(f"Hint: {hint}")
    parts.append(f"Question: {q}" if q else "Question:")
    if qtype == "multi_choice" and choices:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        parts.append("Choices:")
        for i, c in enumerate(choices):
            tag = letters[i] if i < len(letters) else str(i)
            parts.append(f"({tag}) {c}")
        parts.append("Answer with the option letter (A/B/C/...).")
    else:
        parts.append("Answer with the final result.")
    return "\n".join(parts).strip()


def save_image_field(img_field, out_path: Path):
    """
    MathVista 的 image 字段在不同环境里可能是：
    - PIL.Image.Image
    - str: 本地图片路径（HF cache）
    - dict: {'path':..., 'bytes':...} 或类似
    - bytes
    - data:image/...;base64,... (少见)
    """
    if PILImage is None:
        raise RuntimeError("Pillow 未安装：请先 pip install Pillow")

    # 1) PIL image
    if hasattr(img_field, "save"):
        img_field.convert("RGB").save(out_path)
        return

    # 2) dict style
    if isinstance(img_field, dict):
        if img_field.get("path") and os.path.exists(img_field["path"]):
            PILImage.open(img_field["path"]).convert("RGB").save(out_path)
            return
        if img_field.get("bytes"):
            PILImage.open(io.BytesIO(img_field["bytes"])).convert("RGB").save(out_path)
            return
        raise RuntimeError(f"image dict 不包含可用的 path/bytes: keys={list(img_field.keys())}")

    # 3) bytes
    if isinstance(img_field, (bytes, bytearray)):
        PILImage.open(io.BytesIO(img_field)).convert("RGB").save(out_path)
        return

    # 4) str style
    if isinstance(img_field, str):
        # 常见：本地缓存路径
        if os.path.exists(img_field):
            PILImage.open(img_field).convert("RGB").save(out_path)
            return

        # 少见：data url base64
        if img_field.startswith("data:image") and "," in img_field:
            _, b64 = img_field.split(",", 1)
            raw = base64.b64decode(b64)
            PILImage.open(io.BytesIO(raw)).convert("RGB").save(out_path)
            return

        # URL 这种在离线环境无法下载
        raise RuntimeError(
            f"image 是字符串但不是本地文件路径（也不是 data url）。值示例：{img_field[:120]}"
        )

    raise RuntimeError(f"无法识别 image 字段类型：{type(img_field)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="testmini")
    ap.add_argument("--max_num", type=int, default=5)
    ap.add_argument("--out_dir", default="results/mathvista_geo_inputs")
    args = ap.parse_args()

    ds = load_dataset("AI4Math/MathVista", split=args.split)
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    n = 0
    for ex in ds:
        if n >= args.max_num:
            break

        pid = str(ex.get("pid", n))
        task_dir = out_root / pid
        task_dir.mkdir(parents=True, exist_ok=True)

        # 保存图片为 image.png
        img_field = ex.get("image", None)
        if img_field is None:
            raise RuntimeError(f"No 'image' field for pid={pid}. keys={list(ex.keys())}")

        img_path = task_dir / "image.png"
        save_image_field(img_field, img_path)

        prompt = build_prompt(ex)

        # 写 ex.json（geo 模式入口）
        ex_json = {
            "problem_text": prompt,
            "logic_form": {"diagram_logic_form": []},   # MathVista 没有 logic form，先留空
            "image_path_code": "image.png",
            "code": (
                "import matplotlib.pyplot as plt\n"
                "import matplotlib.image as mpimg\n"
                "img = mpimg.imread('image.png')\n"
                "plt.figure(figsize=(6,6))\n"
                "plt.imshow(img)\n"
                "plt.axis('off')\n"
                "plt.show()\n"
            )
        }
        with open(task_dir / "ex.json", "w", encoding="utf-8") as f:
            json.dump(ex_json, f, ensure_ascii=False, indent=2)

        n += 1

    print(f"✅ Wrote {n} geo-style tasks to: {out_root}")


if __name__ == "__main__":
    main()
