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
<a href="https://x.com/GaoYipeng/status/2110979390918320391"><img src="https://img.shields.io/badge/-Twitter@GaoYipeng-black?logo=twitter&logoColor=1D9BF0" alt="Twitter@GaoYipeng"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/Code%20License-Apache%202.0-4C9A2A" alt="Code License: Apache 2.0"></a>

</div>

<br>
<details open>
<summary>💡 Explore our related work on 3D generation and evaluation ✨.</summary>

> [**3DCodeBench: Benchmarking Agentic Procedural 3D Modeling Via Code**](https://www.3dcodebench.com/) <br>
> Yipeng Gao, Lei Shu, Genzhi Ye, et al. <br>
> [NeurIPS 2026, Evaluations & Datasets Track](https://neurips.cc/virtual/2026/poster/139453) <br>
> [![GitHub](https://img.shields.io/badge/-Github-black?logo=github)](https://github.com/gaoypeng/3dcodebench) [![GitHub stars](https://img.shields.io/github/stars/gaoypeng/3dcodebench.svg?style=social)](https://github.com/gaoypeng/3dcodebench) [![arXiv](https://img.shields.io/badge/Arxiv-2606.01057-b31b1b.svg?logo=arXiv)](https://arxiv.org/abs/2606.01057) <br>

</details>

## 📣 News

- [2026.11.11] 3DCodeVerse-2M reaches nearly [1.6 million monthly downloads](https://modelpulse.ifsp.dev/dataset/3dcodeverse/3dcodeverse-2m) on Hugging Face.
- [2026.11.01] We recreate 30 seconds of [OpenAI Futures](#-awesome-showcase) entirely in code with Claude Opus 5.5. Explore the [source code and agent logs](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-awesome-showcase/tree/main/openai-futures-30s).
- [2026.10.15] Our [paper](https://arxiv.org/abs/2610.22341) is now available on arXiv.

## 🎬 Awesome Showcase

Films rebuilt **entirely in code**, with full source and agent logs on [Hugging Face](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-awesome-showcase).

| Code | Reference | Result |
| :-- | :-: | :-: |
| [link](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-awesome-showcase/tree/main/openai-futures-30s) | <img src="assets/showcase/openai_futures_reference.gif" width="360"><br>[OpenAI on X](https://x.com/OpenAI/status/2116253388883345623) | <img src="assets/showcase/openai_futures_opus55.gif" width="360"><br>Opus 5.5 by Code |

## ✨ Highlights

1. **We generate 3D worlds entirely as executable code**, spanning objects, articulated assets, scenes, and graphics. Geometry, materials, motion, and cameras remain editable in source, enabling precise control over the generated world.

2. **We improve 3D generation with the same model backbone**, achieving [90% human preference over Claude Design](https://3dcodeverse.com/#results) with Claude Opus 5.5 across 100 text-to-3D object prompts. Our harness combines execution logs, geometric measurements, and visual judgments to guide targeted repairs.

3. **We release over two million code–condition pairs for training 3D coding models** in [3DCodeVerse-2M](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-2m). Supervised and preference fine-tuning raise Qwen3.8-27B's 3DCodeBench execution rate from **51.9% to 92.9%**.

## 🎬 Video Demos

https://github.com/user-attachments/assets/3b0b5e5f-eed0-4ffd-8ab7-f3c709e15fad

## 🚀 Quick Start

`3dcodeverse` is a plugin for Claude Code, Codex, and Antigravity.
See the [installation guide](harness/docs/INSTALL.md) for prerequisites and the [plugin guide](harness/README.md) for usage.

```bash
git clone https://github.com/gaoypeng/3dcodeverse && cd 3dcodeverse
bash harness/setup.sh                                  # the `3dcode` instruments + runtime_js + headless Chrome, then doctor
claude plugin marketplace add . && claude plugin install 3dcodeverse@3dcodeverse   # Claude Code
codex plugin marketplace add . && codex plugin add 3dcodeverse@3dcodeverse         # Codex
agy plugin install harness                                                         # Antigravity
```

Then ask your agent for a 3D thing, e.g. "Make a mid-century wooden dining chair in Blender bpy."

## 🗂️ Repository Structure

| Directory | Description | Documentation |
| :-- | :-- | :-- |
| [`harness/`](harness/) | The `3dcodeverse` agent plugin: skills, judge subagent, and the `3dcode` build / measure / render / gate / score instruments. | [Plugin guide](harness/README.md) |
| [`flywheel/`](flywheel/) | Reader for runs recorded by the v0.0.x CLI harness: gallery, cost, dataset export, pick. | [Flywheel guide](flywheel/README.md) |
| [`toolkits/`](toolkits/) | General data CLI and per-source corpus curation. | [Data toolkit guide](toolkits/README.md) |
| [`finetune/`](finetune/) | LLaMA-Factory training-data preparation, supervised fine-tuning, and preference optimization for open models. | [Training guide](finetune/README.md) |
| [`eval/`](eval/) | Evaluation of standalone LLMs / VLMs on 3D coding. | [Evaluation guide](eval/README.md) |

## 📚 Citation

If you find 3DCodeVerse useful, please cite:

```bibtex
@article{gao20263dcodeverse,
  title={3DCodeVerse: Exploring 3D Worlds as Code},
  author={Gao, Yipeng and Zeng, Ziyao and Huang, Jinfa and Li, Yijiang and Xiong, Haolin and Qin, Wang and Yu, Xinlei and Yao, Youheng and Zhang, Jie and Lin, Kun-Yu and others},
  journal={arXiv preprint arXiv:2610.22341},
  year={2026}
}
```

## 📄 License

The code is licensed under [Apache-2.0](LICENSE). The published datasets carry their own terms
(CC-BY-NC-SA, non-commercial); see each dataset card.
