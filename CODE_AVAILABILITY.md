# Code and data availability

## Code Availability

Custom processing and validation code supporting TLCD 1.0 is available in the [`code/`](code/) directory of this repository. The released package contains: (i) S5 generation of the three structured event inputs (`EgoInfo.csv`, `ObjInfo.csv`, and `MapInfo.csv`); (ii) S6 construction of `EvidenceChain.csv`; (iii) S7 generation of `record.json`; (iv) documented historical S8+ correction utilities; (v) extraction, fixed-30-Hz conversion, and integrity auditing of the seven camera videos; (vi) VLM-assisted scene-description and driving-suggestion workflows; and (vii) scripts for dataset statistics and technical validation. Drawing and manuscript-figure programs are excluded because they are not required to construct or validate the released dataset.

S1–S4 are outside the published processing boundary: the acquisition system described in the manuscript already stored synchronized observations as event-level records. The public processing chain therefore begins with conversion of those event records into the released files.

No API key is committed. The VLM review scripts read `QWEN_API_KEY` from the runtime environment or an explicitly supplied external credential file. Users are responsible for complying with the selected API provider's terms.

The published VLM folder mirrors the category-specific programs located in the project source tree. Qwen-compatible review entry points are available for maximum speed, minimum speed, following distance, lane change, continuous lane change, and road marking; lateral distance uses a separate data-derived description/consistency pass. The supplied source tree did not contain a standalone Qwen review entry point for overtaking, and the repository does not claim or invent one.

The published video-conversion programs produce an earlier HEVC stage; the refreshed sample files are masked H.264/AVC. The video directory also provides the original utility for promoting already-masked videos into the released layout and a new standalone full-decoding checker for the current H.264 files. The face/plate detector and H.264 masking encoder have not yet been located in the supplied processing sources and are not included. See [`code/video_processing/README.md`](code/video_processing/README.md) for the exact boundaries and commands. S7 outputs also precede final auxiliary-text cleanup and `camera_set_id` assignment; those steps are outside this update. The package should not be interpreted as a complete one-command reconstruction of the refreshed files.

## Data Availability

Representative data are provided in [`samples-v1.1`](https://github.com/SOTIF-AVLab/TLCD/releases/tag/samples-v1.1), containing one complete event for every city–category combination: Nanjing and Changchun across all eight TLCD event categories (16 events in total). Each archive contains current structured files, seven fixed-30-Hz H.264/AVC videos, camera parameters and documentation. The asset manifest, checksums and sample-validation results are provided in [`samples/`](samples/). The full parameter JSON is also directly available at [`camera_parameters.json`](camera_parameters.json).

The full dataset remains available through application rather than unrestricted public download. Researchers may request access by emailing [hong_wang@mail.tsinghua.edu.cn](mailto:hong_wang@mail.tsinghua.edu.cn) or [zhao_cx25@mails.tsinghua.edu.cn](mailto:zhao_cx25@mails.tsinghua.edu.cn) with the subject **[Apply for TLCD] name_country(region)_organization**. Requests should state the applicant's affiliation, research purpose, intended use, and requested data scope. Requests will be reviewed by the project team; approval and transfer conditions depend on the applicable privacy, authorization and data-release review. Refreshing the sample videos does not change this access route.

The DOI, archived dataset record, and final dataset/software licences will be added when available. Until then, cite the repository commit and sample release tag used.

## 中文核对

- 公开代码从 S5 开始，S1–S4 不属于论文定义的发布处理边界。
- 公开样本为两座城市、八类事件各 1 条，共 16 条；`samples-v1.1` 已同步当前数据文件、H.264 视频及相机参数。
- 完整数据继续通过指定邮箱申请，样本更新不等于全量公开。申请路径、邮件格式和审核主体保持不变。
- 视频代码已补打码后发布目录整理工具和当前 H.264 完整解码核验工具；实际检测/打码编码程序尚未定位，不能声称已公开完整视频生成链。最终字段整理和参数关联代码本轮不变。
- DOI 与数据/代码许可证尚未确定，因此文件中未虚构编号或许可证。
