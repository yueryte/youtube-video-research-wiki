---
title: F-Harness
type: concept
tags: [Anthropic, multi-agent, framework, harness-engineering, AI-agent]
summary: Anthropic 的多智能体框架，用于长程任务规划与质量评估
sources:
  - cf7d4c16-7e3f-4e6e-8f5d-394bec460e23
created: 2026-05-12
updated: 2026-05-12
layer: L1
---

# F-Harness

**F-Harness**（Framework Harness）是 Anthropic 提出的一种多智能体协作框架，专门用于解决长耗时、高复杂度的 AI Agent 任务。它是 [[Harness Engineering]] 在实践中的典型代表，通过引入角色分工与独立评估机制，显著提升了 Agent 系统的产出质量与稳定性。

## 核心架构

F-Harness 采用 **Planner-Generator-Evaluator** 三智能体协作模式：

1. **Planner（规划者）**：接收模糊的高层需求，将其拆解为详细、可执行的功能点列表。该角色负责建立任务的结构化蓝图，确保后续执行有明确的方向。
2. **Generator（生成者）**：按照 Planner 输出的功能列表逐一实现代码或内容。Generator 在开发过程中会与 Evaluator 持续沟通，明确交付标准。
3. **Evaluator（评估者）**：作为独立的第三方 Agent，负责客观检查 Generator 的产出。其核心价值在于避免“自卖自夸”导致的 Bug 遗漏，确保质量审计的公正性。

## 成本与效果对比

Anthropic 的实验数据清晰地展示了 F-Harness 的价值：

| 方案 | 耗时 | 成本 | 产出质量 |
|------|------|------|----------|
| **Solo（单模型）** | 20 分钟 | 9 美元 | 基本不可用 |
| **F-Harness** | 6 小时 | 200 美元 | 达到可商用高水准 |

虽然 F-Harness 的计算成本是 Solo 方案的 20 倍以上，但其产出的代码质量与系统稳定性远超单一模型模式。这一结果印证了 [[Harness Engineering]] 的核心观点：**精心设计的系统架构是 Agent 稳定运行的保障**。

## 与 Prompt Engineering 的区别

F-Harness 代表了从 [[Prompt Engineering]] 到系统集成的范式跃迁。前者关注“如何把话对模型说清楚”，而 F-Harness 关注的是“如何围绕模型搭建一整套可运行、可校验、可自愈的系统”。在 F-Harness 中，角色分工、任务拆解、独立评估等系统化设计取代了对提示词模板的依赖。

## 争议与局限

批评者认为，F-Harness 中的任务拆解、独立审计等机制是传统软件工程的成熟技术，只是被 AI 圈重新包装。此外，随着模型能力的提升（如从 Claude 3.5 升级到 3.6），部分原本需要 F-Harness 强制干预的流程（如逐个功能点执行）可能被更强的全局统筹能力所取代。这表明 [[Harness Engineering]] 是一个过渡性解决方案，而非终局答案。