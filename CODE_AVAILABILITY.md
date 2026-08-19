# Code and data availability

## Code Availability

Custom processing and validation code supporting TLCD 1.0 is available in the [`code/`](code/) directory of this repository. The released package contains: (i) S5 generation of the three structured event inputs (`EgoInfo.csv`, `ObjInfo.csv`, and `MapInfo.csv`); (ii) S6 construction of `EvidenceChain.csv`; (iii) S7 generation of `record.json`; (iv) documented historical S8+ correction utilities; (v) extraction, fixed-30-Hz conversion, and integrity auditing of the seven camera videos; (vi) VLM-assisted scene-description and driving-suggestion workflows; and (vii) scripts for dataset statistics and technical validation. Drawing and manuscript-figure programs are excluded because they are not required to construct or validate the released dataset.

S1–S4 are outside the published processing boundary: the acquisition system described in the manuscript already stored synchronized observations as event-level records. The public processing chain therefore begins with conversion of those event records into the released files.

No API key is committed. The VLM review scripts read `QWEN_API_KEY` from the runtime environment or an explicitly supplied external credential file. Users are responsible for complying with the selected API provider's terms.

The published VLM folder mirrors the category-specific programs located in the project source tree. Qwen-compatible review entry points are available for maximum speed, minimum speed, following distance, lane change, continuous lane change, and road marking; lateral distance uses a separate data-derived description/consistency pass. The supplied source tree did not contain a standalone Qwen review entry point for overtaking, and the repository does not claim or invent one.

## Data Availability

Representative data are provided as a GitHub sample release containing one complete event for every city–category combination: Nanjing and Changchun across all eight TLCD event categories (16 events in total). Each archive contains the released structured files and seven final fixed-30-Hz videos. The asset manifest and checksums are provided in [`samples/`](samples/).

The full dataset is not publicly downloadable at this stage because video de-identification and the associated release review are ongoing. Researchers may request access by emailing [hong_wang@mail.tsinghua.edu.cn](mailto:hong_wang@mail.tsinghua.edu.cn) or [zhao_cx25@mails.tsinghua.edu.cn](mailto:zhao_cx25@mails.tsinghua.edu.cn) with the subject **[Apply for TLCD] name_country(region)_organization**. Requests should state the applicant's affiliation, research purpose, intended use, and requested data scope. Requests will be reviewed by the project team; approval and transfer conditions depend on completion of the applicable privacy, authorization, and data-release review.

The DOI, archived dataset record, and final dataset/software licences will be added when available. Until then, cite the repository commit and sample release tag used.

## 中文核对

- 公开代码从 S5 开始，S1–S4 不属于论文定义的发布处理边界。
- 公开样本为两座城市、八类事件各 1 条，共 16 条完整事件；视频后续可以脱敏版本替换。
- 完整数据暂不直接公开的具体原因是视频脱敏和发布审查尚未完成；申请路径、邮件格式和审核主体已明确。
- DOI 与数据/代码许可证尚未确定，因此文件中未虚构编号或许可证。
