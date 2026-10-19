from .base import Detection, Frame, FrameSource


def open_source(cfg) -> FrameSource:
    """Create the frame source named in the config (imx500 or simulator)."""
    kind = cfg["camera"]["source"]
    if kind == "imx500":
        from .imx500 import IMX500Source

        return IMX500Source(cfg)
    if kind == "simulator":
        from .simulator import SimulatorSource

        return SimulatorSource(cfg)
    raise ValueError(f"Bilinmeyen kamera kaynağı: {kind}")


__all__ = ["Detection", "Frame", "FrameSource", "open_source"]
