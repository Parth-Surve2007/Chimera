"""SAM 3.1 Visual-Perception Proof-of-Concept and Investigation for Chimera.

Investigates using SAM 3.1 (Segment Anything Model 3.1) to enhance Chimera's
Windows Computer-Use architecture.

Key Architectural Guarantees:
1. SAM segmentation masks are PERCEPTION EVIDENCE, NOT permission to interact.
2. UI Automation remains the authoritative source of control identity, bounding rects,
   and supported action patterns (e.g. InvokePattern, ValuePattern).
3. Visual perception never bypasses AgentRuntime execution policies, approval checks,
   or Plan Mode boundaries.
4. No coordinate-based clicking or automated desktop input injection.
5. Cross-references detected visual regions with verified UIA elements to yield
   multi-modal grounded confidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VisualRegion:
    label: str
    bounding_box: tuple[int, int, int, int]  # (left, top, right, bottom)
    confidence: float
    mask_present: bool = False


@dataclass(frozen=True)
class CorroboratedControl:
    reference_id: str
    control_type: str
    name: str
    uia_rect: tuple[int, int, int, int]
    visual_match: VisualRegion | None
    iou_score: float
    verified: bool


def calculate_iou(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
    """Calculate Intersection-over-Union between two bounding boxes."""
    xA = max(box_a[0], box_b[0])
    yA = max(box_a[1], box_b[1])
    xB = min(box_a[2], box_b[2])
    yB = min(box_a[3], box_b[3])

    inter_width = max(0, xB - xA)
    inter_height = max(0, yB - yA)
    inter_area = inter_width * inter_height

    area_a = max(0, box_a[2] - box_a[0]) * max(0, box_a[3] - box_a[1])
    area_b = max(0, box_b[2] - box_b[0]) * max(0, box_b[3] - box_b[1])

    union_area = float(area_a + area_b - inter_area)
    if union_area <= 0:
        return 0.0
    return inter_area / union_area


class SAMVisualPerception:
    """Perception grounding engine combining visual segmentation with UIA verification."""

    def __init__(self, iou_threshold: float = 0.5):
        self.iou_threshold = iou_threshold

    def parse_api_response(self, response_data: dict[str, Any]) -> list[VisualRegion]:
        """Parse SAM 3.1 object detection / segmentation API output."""
        regions = []
        objects = response_data.get("objects", []) or response_data.get("segments", [])
        for obj in objects:
            bbox = obj.get("box") or obj.get("bounding_box") or [0, 0, 0, 0]
            label = obj.get("label", "unknown_control")
            conf = float(obj.get("confidence", 0.0))
            has_mask = bool(obj.get("mask"))
            regions.append(VisualRegion(
                label=label,
                bounding_box=tuple(bbox),
                confidence=conf,
                mask_present=has_mask,
            ))
        return regions

    def corroborate_with_uia(
        self,
        visual_regions: list[VisualRegion],
        uia_elements: list[dict[str, Any]],
    ) -> list[CorroboratedControl]:
        """Cross-reference visual perception regions with Windows UIA elements.
        
        UIA elements remain authoritative. Visual regions provide grounded confirmation.
        """
        corroborated = []
        for elem in uia_elements:
            ref_id = elem.get("reference_id", "")
            ctl_type = elem.get("control_type", "")
            name = elem.get("name", "")
            rect = elem.get("rect", [0, 0, 0, 0])
            uia_box = (rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]) if len(rect) == 4 else (0, 0, 0, 0)

            best_match = None
            best_iou = 0.0
            for region in visual_regions:
                iou = calculate_iou(uia_box, region.bounding_box)
                if iou > best_iou:
                    best_iou = iou
                    best_match = region

            is_verified = best_iou >= self.iou_threshold
            corroborated.append(CorroboratedControl(
                reference_id=ref_id,
                control_type=ctl_type,
                name=name,
                uia_rect=uia_box,
                visual_match=best_match,
                iou_score=round(best_iou, 4),
                verified=is_verified,
            ))
        return corroborated
