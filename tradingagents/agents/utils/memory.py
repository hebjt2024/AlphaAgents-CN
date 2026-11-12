# =============================================================================
# 模块名称：memory.py
# 模块功能：基于 BM25 算法的金融情景记忆系统（中文友好分词）
# =============================================================================
#
# ============================================================================
# 前置知识（如果你完全没有 Python 和 LangChain 经验，请先阅读本节）
# ============================================================================
#
# 【什么是"情景记忆"（Episodic Memory）？为什么需要它？】
#   想象一个经验丰富的交易员。他遇到过无数次各种市场情况：
#   - "高通胀+加息时，消费品板块表现好"
#   - "科技股高波动+机构减持时，应该减少成长股敞口"
#   当他遇到新的类似情况时，会回忆起过去的经验，做出更明智的决策。
#
#   本模块就是给 AI 交易系统装上这样的"记忆"：
#   - 存储：把历史上遇到过的金融情景和对应的投资建议存起来
#   - 检索：遇到新情况时，从记忆中找出最相似的历史情景
#   - 借鉴：用历史经验辅助当前决策
#
# 【为什么不用 LLM 自己的记忆？】
#   - LLM 有 token 限制，记不住太多历史
#   - LLM 每次对话是独立的，不会自动记住之前的内容
#   - 用外部记忆系统可以存储无限量的经验，不受 LLM 限制
#
# 【什么是 BM25 算法？（通俗解释）】
#   BM25（Best Matching 25）是一种"文本相似度计算"算法。
#
#   【通俗比喻】想象你是图书管理员，有人来问"有没有关于高通胀和加息的书？"
#   你需要从书架上找出最相关的书。你怎么判断哪本书最相关？
#   - 看书里有没有"通胀"、"加息"这些关键词 → 词频（TF, Term Frequency）
#   - 如果"的"、"了"这种常见词也出现，不应该加分 → 逆文档频率（IDF）
#   - 如果一本书特别长，不应因此就认为它更相关 → 文档长度归一化
#
#   BM25 综合考虑了这三个因素，给每个文档打一个分数，分数越高越相关。
#
#   【BM25 的核心思想（简化版）】
#   1. 词频饱和：一个词出现 10 次不一定比出现 5 次好 2 倍。
#      BM25 用对数函数让词频增长"减速"，避免长文档占便宜。
#   2. 文档长度归一化：短文档中出现某个词，比长文档中出现更"有意义"。
#      BM25 会根据文档长度调整分数。
#   3. 逆文档频率（IDF）：在所有文档中都出现的词（如"的"）重要性低，
#      只在少数文档中出现的词（如"量化宽松"）重要性高。
#
#   【为什么选 BM25 而不是用 LLM 做语义匹配？】
#   - BM25 纯本地运行，不需要调用 LLM API（省钱、快速）
#   - 没有 token 限制，可以存储大量情景
#   - 与 LLM 提供商无关，换 LLM 也能用
#   - 基于词法的匹配速度快，适合实时检索场景
#
# 【什么是分词（Tokenization）？】
#   分词就是把一段文本拆成一个个"词"（token）。
#   例如："High inflation rate" → ["high", "inflation", "rate"]
#   BM25 需要先分词才能计算词频。
#
#   【本模块的分词策略（中文友好）】
#   本系统的所有文档都是中文，因此分词器必须能处理中文。常见的做法是
#   引入 jieba 之类的中文分词词典库，但项目不引入新的第三方依赖，因此
#   这里采用**字符二元组（character bigram）**近似：
#
#     "科技股波动加剧" → ["科技", "技股", "股波", "波动", "动加", "加剧"]
#
#   WHY 字符二元组：
#     - 中文没有空格分词，\b 词边界对中文无效（原先的 r'\b\w+\b' 会把一整句
#       中文变成一个 token，导致 BM25 全部得 0 分、检索形同虚设）。
#     - 二元组把句子切成"有意义的最小片段"，既能命中完全相同的词，
#       也能命中部分重叠的词（"科技行业波动" 与 "科技股波动" 共享
#       "科技"、"波动"）。
#     - 不需要词典、不需要额外依赖，纯 Python 实现。
#
#   TRADE-OFF（代价，必须知道）：
#     - 二元组无法还原真实词边界，"科技股" 与 "科技/股" 会被切成同样的
#       二元组序列，存在少量误召回。
#     - 单字词的语义被稀释（"股" 只以 "科技/股波" 之类的片段出现）。
#     - 若未来允许引入 jieba，可在此处替换 _tokenize 内部实现，其余逻辑
#       （索引、检索、持久化）无需改动。
#
#   英文/数字部分保持原有行为：按词边界（空格、标点）切分并转小写。
#
# 【什么是类（class）和 self？】
#   类是创建对象的"模板"。用 class 定义一个类，里面可以放属性和方法。
#   self 代表"当前对象自己"。在方法内部用 self.属性名 访问对象的属性。
#   例如：self.name = "test" 表示给当前对象设置 name 属性为 "test"。
#
# 【什么是 __init__ 方法？】
#   __init__ 是 Python 类的"构造函数"——创建对象时自动调用。
#   当你写 memory = FinancialSituationMemory("test") 时，
#   Python 自动调用 __init__(self, "test") 来初始化对象。
#
# 【什么是 lambda 函数？】
#   lambda 是 Python 创建"匿名小函数"的语法。
#   例如 lambda i: scores[i] 等价于：
#     def f(i): return scores[i]
#   用于排序时指定排序依据，比单独定义一个函数更简洁。
#
# 【什么是 __name__ == "__main__"？】
#   每个 Python 文件都有一个特殊变量 __name__：
#   - 当文件被直接运行（python memory.py）时，__name__ 等于 "__main__"
#   - 当文件被 import 导入时，__name__ 等于模块名 "memory"
#   所以 if __name__ == "__main__": 下面的代码只在直接运行时执行。
#   这段代码用于测试 FinancialSituationMemory 的功能是否正常。
# ============================================================================
#
# 设计模式：记忆-检索模式（Memory-Retrieval Pattern）
#   - 存储阶段：将（情景，建议）对存入记忆库
#   - 索引阶段：对文档进行分词并建立 BM25 索引
#   - 检索阶段：接收查询，计算相似度，返回最匹配的历史记录
#
# ============================================================================
# 运行时数据流示例
# ============================================================================
#
#   场景：交易系统遇到"科技板块波动加剧"的市场情况
#
#   第 1 步：系统之前存储过历史经验
#     memory.add_situations([
#       ("科技股波动加剧，机构持续减持", "降低成长股敞口"),
#       ("消费板块需求疲软", "关注防御性板块"),
#     ])
#     → BM25 索引被重建，包含两个文档
#
#   第 2 步：遇到新情况，检索相似记忆
#     results = memory.get_memories("科技股波动加剧", n_matches=1)
#     → 对查询分词：["科技", "技股", "股波", "波动"]
#     → BM25 计算每个文档的得分（不匹配文档得 0 分，匹配文档得正分）
#     → "科技股波动加剧，机构持续减持" 得分最高
#     → 返回 [{matched_situation: "科技股波动加剧…", recommendation: "降低成长股敞口",
#              similarity_score: 1.0}]
#
#   第 3 步：系统利用历史经验辅助决策
#     AI 看到"之前类似情况建议降低成长股敞口"，结合当前分析做出决策
# =============================================================================

# ---------------------------------------------------------------------------
# 导入区：每个导入模块的作用说明
# ---------------------------------------------------------------------------

# re: Python 正则表达式模块，用于文本分词（切分 CJK 片段 / 拉丁词）
# re.findall(模式, 文本) 找出文本中所有匹配模式的子串
# math: 标准库数学函数（log），用于计算 BM25 的 IDF
import math
import re

# Counter: 标准库计数器，用于统计词频（TF）与文档频率（DF）
from collections import Counter


# =============================================================================
# 私有类：_BM25Index —— 轻量 BM25 索引
# =============================================================================
class _BM25Index:
    """
    极简 BM25（Okapi 变体）倒排索引。

    【为什么要自己实现，而不是直接用 rank_bm25？】
    rank_bm25 的 ``BM25Okapi`` 使用 ``idf = log((N - df + 0.5) / (df + 0.5))``，
    该值在 ``df > N/2`` 时为**负数**，rank_bm25 会用一个可能为负的 epsilon
    下限去兜底，导致"高频词反而扣分"，最终出现**所有文档同分为 0**的退化
    情况（本项目的小语料库场景极易触发）。

    这里改用 Lucene 风格的正 IDF：
        ``idf = log(1 + (N - df + 0.5) / (df + 0.5))``
    该式恒为正，保证：
      - 真正命中文档的得分一定 > 0（可区分性）；
      - 未命中文档得分为 0，归一化后 similarity_score 恰好是 0.0；
      - 高频词的 IDF 趋近于 0（但不为负），不会反向扣分。

    属性：
        corpus_size (int): 文档数量
        doc_lengths (list[int]): 每篇文档的 token 数
        avgdl (float): 平均文档长度
        term_freqs (list[Counter]): 每篇文档的词频表
        idf (dict[str, float]): 每个 token 的 IDF 权重
    """

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        """
        构建 BM25 索引。

        参数：
            corpus_tokens (list[list[str]]): 已分词的文档集合
            k1 (float): 词频饱和系数，默认 1.5
            b (float): 文档长度归一化强度，默认 0.75
        """
        self.k1 = float(k1)
        self.b = float(b)
        self.corpus_size = len(corpus_tokens)
        self.doc_lengths = [len(tokens) for tokens in corpus_tokens]
        self.avgdl = (
            sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0.0
        )
        self.term_freqs = [Counter(tokens) for tokens in corpus_tokens]

        # 文档频率：每个 token 出现在多少篇文档中
        doc_freq: Counter = Counter()
        for tokens in corpus_tokens:
            doc_freq.update(set(tokens))

        # IDF 权重（Lucene 风格，恒为正）
        self.idf: dict[str, float] = {
            token: math.log(
                1.0 + (self.corpus_size - freq + 0.5) / (freq + 0.5)
            )
            for token, freq in doc_freq.items()
        }

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        """
        计算查询与每篇文档的 BM25 得分。

        参数：
            query_tokens (list[str]): 已分词的查询

        返回：
            list[float]: 与文档等长的得分列表（未命中为 0.0）
        """
        scores = [0.0] * self.corpus_size
        if not query_tokens or not self.corpus_size:
            return scores

        for token in query_tokens:
            weight = self.idf.get(token)
            if weight is None:
                # 查询里出现了语料库没有的 token（OOV），直接跳过
                continue
            for index, counter in enumerate(self.term_freqs):
                freq = counter.get(token, 0)
                if not freq:
                    continue
                length = self.doc_lengths[index]
                # 长度归一化：avgdl 为 0（空文档）时退化为 1，避免除零
                norm = 1.0 - self.b + self.b * (length / self.avgdl if self.avgdl else 1.0)
                denominator = freq + self.k1 * norm
                scores[index] += weight * freq * (self.k1 + 1.0) / denominator
        return scores


# =============================================================================
# FinancialSituationMemory 类 - 金融情景记忆系统
# =============================================================================
#
# ---------------------------------------------------------------------------
# 以下是可视化的流程图：BM25 检索数据流图
# ---------------------------------------------------------------------------
# 该图展示从"添加经验"到"检索经验"的完整数据流，
# 包括分词、索引构建、查询计算、得分排序和归一化等步骤。
# ---------------------------------------------------------------------------
# ```mermaid
# flowchart LR
#     subgraph 存储阶段
#         S1["add_situations()<br/>传入[(情景, 建议)]列表"]
#         S2["documents.append(情景)<br/>recommendations.append(建议)"]
#         S3["_rebuild_index()<br/>重建BM25索引"]
#         S3a["_tokenize()<br/>CJK 片段 → 字符二元组<br/>拉丁片段 → 词边界切分"]
#         S3b["_BM25Index(tokenized_docs)<br/>正 IDF + 长度归一化"]
#         S1 --> S2 --> S3 --> S3a --> S3b
#     end
#
#     subgraph 检索阶段
#         Q1["get_memories()<br/>传入当前情景查询"]
#         Q2["_tokenize()<br/>对查询文本分词"]
#         Q3["bm25.get_scores()<br/>计算每篇文档的BM25得分"]
#         Q4["sorted + reverse=True<br/>按得分降序排列"]
#         Q5["取前n_matches条<br/>score / max_score（max>0 时）"]
#         Q6["返回结果列表<br/>[{matched_situation,<br/>recommendation,<br/>similarity_score}]"]
#         Q1 --> Q2 --> Q3 --> Q4 --> Q5 --> Q6
#     end
#
#     S3b -.->|"索引就绪，可被检索"| Q3
# ```
# ---------------------------------------------------------------------------
# 以下是可视化的流程图：反思存储与检索的闭环时序图
# ---------------------------------------------------------------------------
# 该图展示反思结果存入记忆、下次决策时检索记忆的完整闭环流程。
# 体现了"存储经验 → 检索经验 → 辅助决策"的学习循环。
# ---------------------------------------------------------------------------
# ```mermaid
# sequenceDiagram
#     participant Agent as 智能体(如牛市研究员)
#     participant Reflector as Reflector反思器
#     participant Memory as FinancialSituationMemory
#     participant BM25 as BM25索引
#     participant LLM as 大语言模型
#
#     Note over Agent,BM25: 阶段1：交易完成后的反思与存储
#     Agent->>Reflector: 交易完成，传入current_state和returns_losses
#     Reflector->>Reflector: _extract_current_situation()<br/>提取市场状况文本
#     Reflector->>LLM: _reflect_on_component()<br/>发送收益+决策+市场状况
#     LLM-->>Reflector: 返回反思报告文本
#     Reflector->>Memory: add_situations([(市场状况, 反思报告)])
#     Memory->>Memory: documents.append(情景)<br/>recommendations.append(建议)
#     Memory->>BM25: _rebuild_index()<br/>分词并重建倒排索引
#     BM25-->>Memory: 索引更新完成
#     Memory-->>Reflector: 存储成功
#
#     Note over Memory,Agent: 阶段2：下次交易时的记忆检索
#     Agent->>Memory: get_memories(当前市场状况, n_matches)
#     Memory->>BM25: get_scores(分词后的查询)
#     BM25-->>Memory: 返回各文档得分数组
#     Memory->>Memory: 排序 + 取前N条 + 归一化
#     Memory-->>Agent: 返回最相似的历史反思记录
#     Agent->>Agent: 参考历史反思，结合当前分析做出更优决策
# ```
# ---------------------------------------------------------------------------
class FinancialSituationMemory:
    """
    金融情景记忆系统 - 使用 BM25 算法进行词法相似度匹配

    这个类实现了"记忆-检索"模式：
    1. 存储：将金融情景和对应的投资建议成对存入
    2. 索引：对存储的情景文本进行分词，建立 BM25 倒排索引
    3. 检索：根据当前情景，返回最相似的历史情景及其建议

    属性：
        name (str): 记忆实例的名称标识
        documents (list[str]): 存储的情景文本列表
        recommendations (list[str]): 存储的投资建议列表（与 documents 一一对应）
        bm25 (_BM25Index | None): BM25 索引对象，None 表示尚未建立索引

    设计优势：
        - 纯本地运行，无需外部 API
        - 无 token 限制，可存储大量情景
        - 与 LLM 提供商无关，适用范围广
        - 基于词法的相似度，速度快，适合文本匹配场景
        - 分词对中文友好（字符二元组近似），不依赖第三方词典

    持久化：
        本类**只负责内存中的数据与检索**，不碰任何数据库/文件 I/O。
        需要落盘时调用 :meth:`to_dict` 导出，由
        ``tradingagents.storage.memory_store`` 负责写入 SQLite；
        恢复时调用 :meth:`from_dict`。
    """

    #: CJK 片段（含扩展 A 区与兼容汉字）在文本中的匹配模式。
    _CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
    #: 文本切分：先切出 CJK 片段，再切出拉丁词。
    #: 拉丁片段保留原有 \w 的语义：字母、数字、下划线。
    _SEGMENT_RE = re.compile(
        r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[a-z0-9_]+"
    )

    def __init__(self, name: str, config: dict = None):
        """
        初始化金融情景记忆系统

        参数：
            name (str): 记忆实例的名称标识，用于区分不同的记忆实例
            config (dict, optional): 配置字典，保留此参数仅为 API 兼容性，
                                     BM25 模式下不使用此参数

        初始化内容：
            - self.name: 记录实例名称
            - self.documents: 空列表，用于存储情景文本
            - self.recommendations: 空列表，用于存储投资建议
            - self.bm25: 初始化为 None，表示尚未建立索引
        """
        # 记录记忆实例的名称（用于区分不同实例，如"牛市记忆"、"熊市记忆"）
        self.name = name
        # 情景文本列表（语料库）
        # 【什么是语料库？】一组文本的集合，BM25 会基于这些文本建立索引
        # 初始为空列表 []，后续通过 add_situations 添加内容
        self.documents: list[str] = []
        # 投资建议列表（与 documents 一一对应）
        # documents[0] 的情景对应 recommendations[0] 的建议
        # 这两个列表通过索引位置保持对应关系
        self.recommendations: list[str] = []
        # BM25 索引对象，初始为空（还没有文档，无法建索引）
        # 添加文档后会通过 _rebuild_index() 创建 _BM25Index 对象
        self.bm25 = None

    # -------------------------------------------------------------------------
    # 分词
    # -------------------------------------------------------------------------
    def _tokenize(self, text: str) -> list[str]:
        """
        对文本进行分词处理，供 BM25 索引使用

        分词策略（中英文混合）：
        - 文本统一转小写，保证大小写不敏感
        - CJK 片段（连续汉字）切成**字符二元组**：
          "科技股波动" → ["科技", "技股", "股波", "波动"]
          单字片段保留该单字本身。
        - 拉丁/数字片段按词边界切分：
          "Tech stocks rising!" → ["tech", "stocks", "rising"]
        - 两者混合时互不影响："RSI超买" → ["rsi", "超买"]

        WHY 不使用 jieba：项目不引入新的第三方依赖。
        WHY 使用二元组而不是整句：整句会退化成单个 token，BM25 全部得 0 分。
        代价见模块头部说明（无法还原真实词边界）。

        参数：
            text (str): 待分词的原始文本

        返回：
            list[str]: 分词后的 token 列表
        """
        if not text:
            return []

        lowered = str(text).lower()
        tokens: list[str] = []
        for match in self._SEGMENT_RE.finditer(lowered):
            segment = match.group(0)
            if self._CJK_RE.fullmatch(segment):
                # 中文片段 → 字符二元组
                if len(segment) == 1:
                    tokens.append(segment)
                else:
                    tokens.extend(
                        segment[index : index + 2] for index in range(len(segment) - 1)
                    )
            else:
                # 拉丁/数字片段 → 整体作为一个 token
                tokens.append(segment)
        return tokens

    def _rebuild_index(self):
        """
        重建 BM25 索引

        当文档列表发生变化（添加新文档）后，需要调用此方法重建索引。
        BM25 索引是"全量"索引，不是增量索引，因此每次文档变化都需要完整重建。

        索引构建过程：
        1. 对所有文档进行分词
        2. 将分词后的文档列表传入 _BM25Index 构造函数
        3. _BM25Index 内部会计算文档频率（DF）和文档长度等统计信息

        特殊情况：
        - 如果文档列表为空，将 bm25 设为 None
        """
        # 检查文档列表是否非空
        if self.documents:
            tokenized_docs = [self._tokenize(doc) for doc in self.documents]
            self.bm25 = _BM25Index(tokenized_docs)
        else:
            # 如果没有文档，将索引设为 None
            self.bm25 = None

    # -------------------------------------------------------------------------
    # 写入
    # -------------------------------------------------------------------------
    def add_situations(self, situations_and_advice: list[tuple[str, str]]):
        """
        添加金融情景及其对应的投资建议

        这是记忆系统的"写入"接口。每次调用都会将新的（情景，建议）对追加到
        现有记忆中，然后重建索引以包含新数据。

        参数：
            situations_and_advice (list[tuple[str, str]]): 元组列表，
                每个元组包含两个元素：
                - 第一个元素 (str): 金融情景描述，如 "科技股波动加剧，机构减持"
                - 第二个元素 (str): 对应的投资建议，如 "降低成长股敞口"

        使用示例：
            memory.add_situations([
                ("科技股波动加剧，机构持续减持", "降低成长股敞口"),
                ("消费板块需求疲软", "关注防御性板块"),
            ])
        """
        for situation, recommendation in situations_and_advice:
            self.documents.append(situation)
            self.recommendations.append(recommendation)

        # 文档列表已更新，重建 BM25 索引以包含新文档
        self._rebuild_index()

    # -------------------------------------------------------------------------
    # 读取
    # -------------------------------------------------------------------------
    def get_memories(self, current_situation: str, n_matches: int = 1) -> list[dict]:
        """
        根据当前情景检索最匹配的历史记忆

        检索流程：
        1. 对当前情景进行分词
        2. 使用 BM25 索引计算每条历史文档的得分
        3. 按得分降序排列，取前 n_matches 条
        4. 对得分进行归一化处理（0-1 范围）
        5. 返回包含匹配结果的结构化字典列表

        归一化规则（关键）：
        - 最高得分 max_score > 0 时：similarity_score = score / max_score
          （命中文档得 1.0 或介于 0~1 之间的正分，未命中文档得 0.0）
        - 最高得分 max_score == 0 时（没有任何文档命中）：全部返回 0.0，
          **不会**出现除零错误，也不会把 0 分伪装成有效相似度。

        参数：
            current_situation (str): 当前的金融情景描述
            n_matches (int): 需要返回的匹配记录数量，默认为 1

        返回：
            list[dict]: 匹配结果列表，每个字典包含：
                - "matched_situation" (str): 匹配到的历史情景
                - "recommendation" (str): 对应的投资建议
                - "similarity_score" (float): 归一化后的相似度得分（0-1）
                - "bm25_score" (float): 原始 BM25 得分（便于调试与排序）
        """
        # 边界检查：如果没有文档或索引未建立，直接返回空列表
        if not self.documents or self.bm25 is None:
            return []

        # 步骤1：对当前情景进行分词，得到查询 token 列表
        query_tokens = self._tokenize(current_situation)

        # 步骤2：使用 BM25 索引计算所有文档的得分
        scores = [float(value) for value in self.bm25.get_scores(query_tokens)]

        # 步骤3：按得分降序取前 n_matches 条
        top_indices = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[: max(0, int(n_matches))]

        # 步骤4：归一化。
        # 只有在最高分为正时才做除法归一化；最高分为 0 时全部记 0.0。
        max_score = max(scores) if scores else 0.0
        if max_score <= 0:
            max_score = 0.0

        results = []
        for idx in top_indices:
            raw_score = scores[idx]
            normalized_score = raw_score / max_score if max_score > 0 else 0.0
            results.append(
                {
                    "matched_situation": self.documents[idx],       # 匹配到的历史情景
                    "recommendation": self.recommendations[idx],    # 对应的投资建议
                    "similarity_score": normalized_score,           # 归一化相似度得分
                    "bm25_score": round(raw_score, 6),             # 原始 BM25 得分
                }
            )

        return results

    # -------------------------------------------------------------------------
    # 清空
    # -------------------------------------------------------------------------
    def clear(self):
        """
        清空所有存储的记忆

        将所有内部状态重置为初始状态：
        - 文档列表清空
        - 建议列表清空
        - BM25 索引设为 None
        """
        self.documents = []
        self.recommendations = []
        self.bm25 = None

    def reset(self):
        """``clear()`` 的别名，语义更贴近"重置到初始状态"。"""
        self.clear()

    # -------------------------------------------------------------------------
    # 序列化（持久化由 storage 层负责，本类不碰 I/O）
    # -------------------------------------------------------------------------
    def to_dict(self) -> dict:
        """
        导出记忆内容为可 JSON 序列化的字典。

        返回：
            dict: {"name": ..., "documents": [...], "recommendations": [...]}
        """
        return {
            "name": self.name,
            "documents": list(self.documents),
            "recommendations": list(self.recommendations),
        }

    @classmethod
    def from_dict(cls, data: dict, config: dict = None) -> "FinancialSituationMemory":
        """
        从 :meth:`to_dict` 的输出恢复记忆实例。

        参数：
            data (dict): 之前导出的字典
            config (dict, optional): 兼容参数（当前实现不使用）

        返回：
            FinancialSituationMemory: 恢复后的记忆实例（索引已重建）
        """
        memory = cls(str(data.get("name") or "memory"), config)
        documents = [str(item) for item in (data.get("documents") or [])]
        recommendations = [
            str(item) for item in (data.get("recommendations") or [])
        ]
        # 允许 recommendations 缺失或长度不一致：缺失部分补空串
        if len(recommendations) < len(documents):
            recommendations.extend([""] * (len(documents) - len(recommendations)))
        memory.documents = documents[: len(recommendations)]
        memory.recommendations = recommendations[: len(memory.documents)]
        memory._rebuild_index()
        return memory

    def __len__(self) -> int:
        """返回记忆条目数量。"""
        return len(self.documents)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"FinancialSituationMemory(name={self.name!r}, "
            f"entries={len(self.documents)})"
        )


# =============================================================================
# 模块自测代码（仅在直接运行此文件时执行）
# =============================================================================
if __name__ == "__main__":
    # 创建一个记忆实例，名称为 "test_memory"
    matcher = FinancialSituationMemory("test_memory")

    # 准备示例数据：四个（情景，建议）元组
    example_data = [
        (
            # 情景1：高通胀 + 加息 + 消费下降
            "High inflation rate with rising interest rates and declining consumer spending",
            # 建议1：考虑防御性板块，审视固收久期
            "Consider defensive sectors like consumer staples and utilities. Review fixed-income portfolio duration.",
        ),
        (
            # 情景2：科技板块高波动 + 机构减持
            "Tech sector showing high volatility with increasing institutional selling pressure",
            # 建议2：减少高增长科技股敞口，寻找价值机会
            "Reduce exposure to high-growth tech stocks. Look for value opportunities in established tech companies with strong cash flows.",
        ),
        (
            # 情景3：强美元 + 新兴市场波动
            "Strong dollar affecting emerging markets with increasing forex volatility",
            # 建议3：对冲汇率风险，减少新兴市场债券配置
            "Hedge currency exposure in international positions. Consider reducing allocation to emerging market debt.",
        ),
        (
            # 情景4：板块轮动 + 收益率上升
            "Market showing signs of sector rotation with rising yields",
            # 建议4：再平衡投资组合，增加受益于高利率的板块
            "Rebalance portfolio to maintain target allocations. Consider increasing exposure to sectors benefiting from higher rates.",
        ),
    ]

    # 将示例数据添加到记忆系统中
    matcher.add_situations(example_data)

    # 准备一个查询情景（当前市场状况）
    current_situation = """
    Market showing increased volatility in tech sector, with institutional investors
    reducing positions and rising interest rates affecting growth stock valuations
    """

    try:
        # 检索最匹配的 2 条历史记忆
        recommendations = matcher.get_memories(current_situation, n_matches=2)

        for i, rec in enumerate(recommendations, 1):
            print(f"\nMatch {i}:")
            print(f"Similarity Score: {rec['similarity_score']:.2f}")
            print(f"Matched Situation: {rec['matched_situation']}")
            print(f"Recommendation: {rec['recommendation']}")

        # 中文自测：中文检索必须有区分度（最高分 > 0）
        zh_memory = FinancialSituationMemory("zh_memory")
        zh_memory.add_situations(
            [
                ("科技股波动加剧，机构持续减持", "降低成长股敞口"),
                ("消费板块需求疲软，量价背离", "关注防御性板块"),
            ]
        )
        zh_hits = zh_memory.get_memories("科技股波动加剧", n_matches=2)
        print("\n[中文检索] 最高相似度：", zh_hits[0]["similarity_score"])
        print("[中文检索] 命中情景：", zh_hits[0]["matched_situation"])

    except Exception as e:
        # 捕获并打印任何异常
        print(f"Error during recommendation: {str(e)}")
