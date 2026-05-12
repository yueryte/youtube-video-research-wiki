---
title: Anthropic
type: entity
tags: [AI安全公司, F-Harness, 长程Agent, 多智能体协作]
summary: AI安全公司，提出F-Harness架构，专注长程Agent系统稳定性
sources:
  - cf7d4c16-7e3f-4e6e-8f5d-394bec460e23
created: 2026-05-12
updated: 2026-05-12
layer: L1
---

# Anthropic

**Anthropic** 是一家专注于 AI 安全的研究公司，以开发可靠、可解释的大语言模型（如 Claude 系列）而闻名。在 [[Harness Engineering]] 领域，Anthropic 提出了 **F-Harness（Framework Harness）** 架构，专门用于解决长耗时、高复杂度任务的 Agent 系统稳定性问题。

## 核心贡献：F-Harness 架构

Anthropic 的实验表明，在长程运行场景下，单一模型（Solo）模式虽然成本低（20 分钟，9 美元），但产出基本不可用。而采用 F-Harness 架构后，虽然耗时延长至 6 小时、成本升至 200 美元，但产出达到了可商用的高水准。

### 多智能体协作模式

F-Harness 的核心是引入**多智能体分工**，将任务拆解为三个角色：

1. **Planner（规划者）**：将模糊的需求拆解为详细的功能点列表。
2. **Generator（生成者）**：按照列表逐一实现功能，并与评估者讨论交付标准。
3. **Evaluator（评估者）**：独立的第三方 Agent，负责客观检查产出，避免“自卖自夸”导致的 Bug 遗漏。

这种架构本质上是一种 [[Context Engineering]] 的延伸——通过系统化的角色分配和验证闭环，确保模型在长程任务中不偏离轨道。

## 与 OpenAI 的对比

| 维度 | Anthropic | OpenAI |
|------|-----------|--------|
| 核心关注 | 长程任务稳定性 | 大规模代码生成效率 |
| 关键架构 | F-Harness（多智能体） | 单一 Agent + 自愈闭环 |
| 成本策略 | 接受高成本换取高质量 | 追求效率与成本平衡 |
| 验证方式 | 独立 Evaluator Agent | Chrome DevTools + Lint |

两者共同验证了 [[Harness Engineering]] 的核心公式：**Harness = Agent - Model**，即系统架构的质量决定了 Agent 的最终表现。

## 争议与局限

尽管 F-Harness 在长程任务中表现优异，但批评者认为其本质是“新瓶装旧酒”——任务拆解、独立验证等均是传统软件工程成熟技术。此外，随着模型能力提升（如 Claude 3.6 Opus），原本需要 F-Harness 干预的“逐个功能点执行”流程，可能被更强的全局统筹能力取代。

## 未来方向

Anthropic 的研究暗示，[[AI Agent]] 系统的演进方向是：**模型越强，所需的 Harness 越少**。但至少在现阶段，F-Harness 仍是实现长程稳定运行的最现实方案。