# Camera parameters

[`camera_parameters.json`](camera_parameters.json) contains seven parameter sets, each with seven camera views. Select the set using the `camera_set_id` in an event's `*_record.json`, then select the camera using the video's `video_tag`. Do not infer the parameter set from the city alone: vehicle 22 has two sets.

The JSON is distributed unchanged from the dataset root. It contains video-resolution intrinsics, distortion coefficients, camera-to-ego extrinsics, inverse transforms, coordinate conventions and parameter-use conditions. The Chinese field dictionary is in [`DatasetInfo.md`](DatasetInfo.md#c2-相机参数).

## Video names and geometry

For `video_<tag>_event_NNN.mp4`, use `<tag>` as `video_tag`.

| Video tag | Viewing direction | Video size (width × height, pixels) | Adopted processing convention |
| --- | --- | --- | --- |
| `30` | Front, 30° narrow view | 1408 × 792 | Rectified; effective K and zero D |
| `120` | Front, 120° wide view | 1408 × 792 | Distortion retained; conditional K |
| `rear` | Rear | 1920 × 1200 | Distortion retained |
| `Side_F` | Left-front | 1920 × 1200 | Distortion retained |
| `Side_L` | Left-rear | 1920 × 1200 | Distortion retained |
| `Side_R` | Right-front | 1920 × 1200 | Distortion retained |
| `Side_B` | Right-rear | 1920 × 1200 | Distortion retained |

These sizes describe the distributed videos, not the sensor's native acquisition resolution. The refreshed samples use H.264/AVC in MP4 at 30 frames/s. Video frames and 100-Hz structured samples are associated by event-relative time; they are not in one-to-one correspondence.

## Parameter sets

| Name | `camera_set_id` |
| --- | --- |
| `vehicle_22_set_1` | `camera_017bfd08f0c7` |
| `vehicle_22_set_2` | `camera_8613fcc9f76c` |
| `vehicle_27` | `camera_921a87bc52d9` |
| `vehicle_28` | `camera_98a4ec8935ef` |
| `vehicle_31` | `camera_d207758e4ce3` |
| `vehicle_PP16` | `camera_c5816f8fa3c8` |
| `vehicle_OTT73` | `camera_2f758d838450` |

## Fields and coordinates

| Field | Meaning |
| --- | --- |
| `image_size` | `[width, height]` in pixels |
| `camera_intrinsic_video` | 3 × 3 intrinsic matrix K at `image_size`; focal lengths and principal-point coordinates in pixels |
| `distortion_coefficients_video` | Eight dimensionless coefficients, ordered `[k1, k2, p1, p2, k3, k4, k5, k6]` |
| `rotation_camera_to_ego` | 3 × 3 rotation R from camera coordinates to ego coordinates |
| `translation_camera_to_ego` | Camera origin in ego coordinates, in metres |
| `rotation_quaternion_xyzw` | Rotation quaternion, ordered `[x, y, z, w]` |
| `T_camera_to_ego` | 4 × 4 homogeneous camera-to-ego transform |
| `T_ego_to_camera` | Inverse 4 × 4 transform |
| `video_projection_model` | `pinhole_rectified` or `opencv_rational_8` |
| `video_processing_status` | `rectified` or `residual_distortion` |
| `camera_intrinsic_video_status` | Status and conditions attached to K; read with `intrinsics_usage_conditions` |

Camera axes are x right, y down, z forward, with the origin at the optical centre. Ego axes are x forward, y left, z up, with the origin at the rear-axle centre projected onto the ground.

Using column vectors, `p_ego = R_camera_to_ego @ p_camera + translation_camera_to_ego`. To project an ego-coordinate point into an image, first transform it using `T_ego_to_camera`, then apply the selected camera's projection model, K and D.

## Use conditions

- **Front 30°:** use the supplied K as effective rectified-video intrinsics and the eight zero distortion coefficients. This is the dataset's adopted usage convention; do not undistort this view again.
- **Front 120°:** K assumes proportional resizing of the full frame without an additional crop or reprojection. Its status is `conditional_full_frame_resize`; the export pipeline's geometric assumption has not been independently established.
- **Rear and side views:** use the supplied K and eight-parameter distortion coefficients at 1920 × 1200.
- Further resizing, cropping or undistortion requires matching output intrinsics. The parameter file does not provide an independent reprojection-accuracy evaluation.

## Lookup example

Run from the repository root, or from a directory containing an extracted sample archive:

```python
import json
from pathlib import Path

record_path = next(Path("Nanjing_valid").rglob("*_record.json"))
record = json.loads(record_path.read_text(encoding="utf-8"))
database = json.loads(Path("camera_parameters.json").read_text(encoding="utf-8"))
sets = {item["camera_set_id"]: item for item in database["camera_sets"]}
camera_set = sets[record["camera_set_id"]]
cameras = {item["video_tag"]: item for item in camera_set["cameras"]}
front_wide = cameras["120"]
K = front_wide["camera_intrinsic_video"]
D = front_wide["distortion_coefficients_video"]
T_ego_to_camera = front_wide["T_ego_to_camera"]
```

Change `Nanjing_valid` to `Changchun_valid` for a Changchun sample. Sample archives include the same root-level parameter JSON and field dictionary so that camera references remain usable after a standalone download.
