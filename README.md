<div align="center">

<img src="assets/wordmark.png" alt="3DCodeVerse" width="60%">

<h2>Exploring 3D Worlds as Code</h2>

<p>
<a href="https://gaoypeng.github.io/"><b>Yipeng Gao</b></a><sup>1*†</sup>, <a href="https://adonis-galaxy.github.io/homepage/"><b>Ziyao Zeng</b></a><sup>2*</sup>, <a href="https://infaaa.github.io/"><b>Jinfa Huang</b></a><sup>3*</sup>, <a href="https://williamium3000.github.io/"><b>Yijiang Li</b></a><sup>4*</sup><br>
<a href="https://haolinxiong.com/">Haolin Xiong</a><sup>1</sup>, <a href="https://www.linkedin.com/in/wang-qin">Wang Qin</a><sup>1</sup>, <a href="https://xinleiyuleo.com/">Xinlei Yu</a><sup>1</sup>, <a href="https://lionelyao.github.io/">Youheng Yao</a><sup>2</sup>, <a href="https://www.linkedin.com/in/jie-zhang-6907a1328">Jie Zhang</a><sup>5</sup>, <a href="https://kunyulin.github.io/">Kun-Yu Lin</a><sup>6</sup>, <a href="https://rongliu-leo.github.io/">Rong Liu</a><sup>1</sup>, <a href="https://yzhao321.github.io/">Yinyuan Zhao</a><sup>7</sup>, <a href="https://www.yajie-zhao.com/">Yajie Zhao</a><sup>1</sup><br>
<a href="https://meiqiguo.github.io/">Meiqi Guo</a><sup>8</sup>, <a href="https://www.cs.rochester.edu/u/jluo/">Jiebo Luo</a><sup>3</sup>, <a href="https://leishu02.github.io/">Lei Shu</a><sup>8</sup>, <a href="https://gyhandy.github.io/">Yunhao Ge</a><sup>1</sup>, <a href="https://scholar.google.com/citations?user=YK4oCiwAAAAJ&amp;hl=en">Xinyu Hu</a><sup>9</sup>, <a href="https://ilab.usc.edu/itti/">Laurent Itti</a><sup>1</sup>
</p>

<p>
<sup>1</sup>University of Southern California · <sup>2</sup>Yale University · <sup>3</sup>University of Rochester<br>
<sup>4</sup>University of California, San Diego · <sup>5</sup>Columbia University · <sup>6</sup>Nanyang Technological University<br>
<sup>7</sup>University of California, Santa Cruz · <sup>8</sup>Google DeepMind · <sup>9</sup>Philo Labs<br>
<sup>*</sup>Equal contribution · <sup>†</sup>Project lead
</p>

<a href="https://arxiv.org/pdf/2610.22341"><img src="https://img.shields.io/badge/arXiv-2610.22341-B31B1B?logo=arxiv&logoColor=white" alt="arXiv"></a>
<a href="https://3dcodeverse.com"><img src="assets/badges/project_page.svg" alt="Project Page"></a>
<a href="https://huggingface.co/datasets/3dcodeverse/3dcodeverse-2m"><img src="assets/badges/hugging_face.svg" alt="Hugging Face"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/Code%20License-Apache%202.0-4C9A2A" alt="Code License: Apache 2.0"></a>

</div>

https://github.com/user-attachments/assets/3b0b5e5f-eed0-4ffd-8ab7-f3c709e15fad

## Awesome Showcase

Films rebuilt **entirely in code**, with full source and agent logs on [Hugging Face](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-awesome-showcase).

| Code | Reference | Result |
| :-- | :-: | :-: |
| [Link](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-awesome-showcase/tree/main/openai-futures-30s) | <img src="assets/showcase/openai_futures_reference.gif" width="360"><br>[OpenAI on X](https://x.com/OpenAI/status/2116253388883345623) | <img src="assets/showcase/openai_futures_opus55.gif" width="360"><br>Opus 5.5 by Code |

## News

- **[10/15/2026]** Our [paper](https://arxiv.org/abs/2610.22341) is now available on arXiv.

## Results

<p align="center"><img src="assets/main_results.png" alt="Main results across four 3D coding tasks" width="90%"></p>

**Fine-tuning on 3DCodeVerse-2M makes open models execute.** The table reports execution rate (%)
on 805 fixed tasks:

| Model | 3DCodeBench | Blender | CadQuery | OpenSCAD | GLSL | Three.js |
| :-- | :-: | :-: | :-: | :-: | :-: | :-: |
| Qwen3.5-9B | 0.0 | 1.9 | 10.5 | 50.0 | 33.0 | 72.5 |
| &nbsp;&nbsp;+ SFT | **84.9** | **90.3** | **98.5** | **86.0** | **56.0** | **74.5** |
| Qwen3.8-27B | 51.9 | 74.8 | 65.5 | 80.0 | 57.0 | 72.5 |
| &nbsp;&nbsp;+ SFT + DPO | **92.9** | **93.2** | **98.5** | **90.0** | **71.5** | **92.5** |

## Repository Structure

| Directory | Description | Documentation |
| :-- | :-- | :-- |
| [`harness/`](harness/) | Agent-based 3D content generation, execution, rendering, and verification. | [Harness guide](harness/README.md) |
| [`toolkits/`](toolkits/) | General data CLI and per-source corpus curation. | [Data toolkit guide](toolkits/README.md) |
| [`finetune/`](finetune/) | LLaMA-Factory training-data preparation, supervised fine-tuning, and preference optimization for open models. | [Training guide](finetune/README.md) |
| [`eval/`](eval/) | Benchmarks and evaluation pipelines for the harness and standalone models. | [Evaluation guide](eval/README.md) |

## Citation

If you find 3DCodeVerse useful, please cite:

```bibtex
@misc{gao2026_3dcodeverse,
  title  = {{3DCodeVerse}: Exploring {3D} Worlds as Code},
  author = {Gao, Yipeng and Zeng, Ziyao and Huang, Jinfa and Li, Yijiang and Xiong, Haolin and
            Qin, Wang and Yu, Xinlei and Yao, Youheng and Zhang, Jie and Lin, Kun-Yu and
            Liu, Rong and Zhao, Yinyuan and Zhao, Yajie and Guo, Meiqi and Luo, Jiebo and
            Shu, Lei and Ge, Yunhao and Hu, Xinyu and Itti, Laurent},
  year   = {2026},
  eprint = {2610.22341},
  archivePrefix = {arXiv},
  primaryClass = {cs.CV},
  doi    = {10.48550/arXiv.2610.22341},
  url    = {https://arxiv.org/abs/2610.22341}
}
```

## License

The code is licensed under [Apache-2.0](LICENSE). The published datasets carry their own terms
(CC-BY-NC-SA, non-commercial); see each dataset card.
