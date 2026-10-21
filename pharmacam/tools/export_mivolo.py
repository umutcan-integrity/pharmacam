"""Export MiVOLO v2 (face + whole figure → age and gender) to ONNX for the device.

Runs once on a development machine, not on the Pi (the Pi only needs onnxruntime):

    pip install torch "transformers==4.51.0" "accelerate==1.8.1" "timm==0.8.13.dev0" onnx onnxruntime
    git clone https://github.com/WildChlamydia/MiVOLO && export PYTHONPATH=$PWD/MiVOLO
    python -m pharmacam.tools.export_mivolo models/mivolo_v2_384.onnx [--int8]

The network takes the face and the person side by side as one 6-channel 384×384 image
(face first; a missing one is a black image) and returns [male, female, age] logits.
The age scaling it was trained with is written into the file's metadata.
Model: huggingface.co/iitolstykh/mivolo_v2 (Apache-2.0 on the model card).
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

REPO = "iitolstykh/mivolo_v2"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--int8", action="store_true", help="also write a dynamically quantised copy (*_int8.onnx)")
    args = ap.parse_args()

    import numpy as np
    import onnx
    import torch
    from transformers import AutoConfig, AutoModelForImageClassification

    cfg = AutoConfig.from_pretrained(REPO, trust_remote_code=True)
    model = AutoModelForImageClassification.from_pretrained(REPO, trust_remote_code=True, torch_dtype=torch.float32)
    net = model.mivolo.model.eval()  # (N, 6, 384, 384) → (N, 3)
    x = torch.randn(1, 6, cfg.input_size, cfg.input_size)  # one person at a time; a fixed shape keeps Fold exportable
    fold = torch.nn.functional.fold  # VOLO passes traced sizes; ONNX Col2Im needs constants
    torch.nn.functional.fold = lambda t, output_size, *a, **k: fold(t, tuple(int(s) for s in output_size), *a, **k)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(net, (x,), str(args.out), input_names=["input"], output_names=["head"],
                      opset_version=18, dynamo=False)

    m = onnx.load(str(args.out))
    meta = {"source": REPO, "input_size": cfg.input_size, "min_age": cfg.min_age, "max_age": cfg.max_age,
            "avg_age": cfg.avg_age, "gender": "male,female", "mean": "0.485,0.456,0.406", "std": "0.229,0.224,0.225"}
    for k, v in meta.items():
        p = m.metadata_props.add()
        p.key, p.value = k, str(v)
    onnx.save(m, str(args.out))

    import onnxruntime as ort

    with torch.no_grad():
        ref = net(x).numpy()
    sess = ort.InferenceSession(str(args.out), providers=["CPUExecutionProvider"])
    got = sess.run(None, {"input": x.numpy()})[0]
    print(f"{args.out}: {args.out.stat().st_size / 1e6:.1f} MB, max |torch - onnx| = {np.abs(ref - got).max():.2e}")
    one = x[:1].numpy()
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    s1 = ort.InferenceSession(str(args.out), sess_options=so, providers=["CPUExecutionProvider"])
    s1.run(None, {"input": one})
    t0 = time.perf_counter()
    for _ in range(5):
        s1.run(None, {"input": one})
    print(f"  1 thread: {(time.perf_counter() - t0) / 5 * 1000:.0f} ms per person")

    if args.int8:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        from onnxruntime.quantization.shape_inference import quant_pre_process

        q = args.out.with_name(args.out.stem + "_int8.onnx")
        pre = q.with_name(q.stem + "_pre.onnx")
        quant_pre_process(str(args.out), str(pre), skip_symbolic_shape=True)
        # only the weights of the matrix products, one scale per output channel: quantising the
        # convolutions too, with one scale per tensor, flipped 26 % of the gender decisions
        quantize_dynamic(str(pre), str(q), weight_type=QuantType.QInt8, per_channel=True,
                         op_types_to_quantize=["MatMul", "Gemm"])
        pre.unlink()
        sq = ort.InferenceSession(str(q), providers=["CPUExecutionProvider"])
        gq = sq.run(None, {"input": x.numpy()})[0]
        print(f"{q}: {q.stat().st_size / 1e6:.1f} MB, max |float - int8| = {np.abs(got - gq).max():.2e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
