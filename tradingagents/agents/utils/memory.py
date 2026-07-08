# =============================================================================
# 模块名称：memory.py
# 模块功能：基于 BM25 算法的金融情景记忆系统
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
#   【为什么不用 LLM 自己的记忆？】
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
# 【什么是 rank_bm25 库？】
#   rank_bm25 是 BM25 算法的 Python 实现库。
#   BM25Okapi 是其中最常用的变体（Okapi 是一个信息检索系统的名字）。
#   使用方式：
#     1. 创建索引：bm25 = BM25Okapi(分词后的文档列表)
#     2. 查询得分：scores = bm25.get_scores(分词后的查询)
#     3. 得分越高表示文档与查询越相关
#
# 【什么是分词（Tokenization）？】
#   分词就是把一段文本拆成一个个"词"（token）。
#   例如："High inflation rate" → ["high", "inflation", "rate"]
#   BM25 需要先分词才能计算词频。本模块用正则表达式做简单分词。
#
# 【什么是正则表达式（Regular Expression, regex）？】
#   正则表达式是一种描述文本模式的"微型语言"。
#   例如 r'\b\w+\b' 的含义：
#   - \b 表示"单词边界"（字母和非字母的分界处）
#   - \w+ 表示"一个或多个字母/数字/下划线"
#   - 整体含义：匹配所有完整的"单词"
#   re.findall(r'\b\w+\b', text) 就是找出文本中所有单词。
#
# 【什么是类（class）和 self？】
#   类是创建对象的"模板"。用 class 定义一个类，里面可以放属性和方法。
#   self 代表"当前对象自己"。在方法内部用 self.属性名 访问对象的属性。
#   例如：self.name = "test" 表示给当前对象设置 name 属性为 "test"。
#
# 【什么是 __init__ 方法？】
#   __init__ 是 Python 类的"构造函数"——创建对象时自动调用的初始化方法。
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
#   所以 if __name__ == "__main__": 下面的代码只在直接运行时执行，
#   被 import 时不会执行。这用于放测试代码。
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
#       ("Tech sector high volatility", "Reduce tech exposure"),
#       ("High inflation", "Consider defensive sectors"),
#     ])
#     → BM25 索引被重建，包含两个文档
#
#   第 2 步：遇到新情况，检索相似记忆
#     results = memory.get_memories("Tech sector showing volatility", n_matches=1)
#     → 对查询分词：["tech", "sector", "showing", "volatility"]
#     → BM25 计算每个文档的得分
#     → "Tech sector high volatility" 得分最高（共享 "tech", "sector", "volatility"）
#     → 返回 [{matched_situation: "Tech sector...", recommendation: "Reduce..."}]
#
#   第 3 步：系统利用历史经验辅助决策
#     AI 看到"之前类似情况建议减少科技股敞口"，结合当前分析做出决策
# =============================================================================

# ---------------------------------------------------------------------------
# 导入区：每个导入模块的作用说明
# ---------------------------------------------------------------------------

# BM25Okapi: BM25 算法的 Python 实现
# 使用 Okapi 变体（最常用的 BM25 版本），参数 k1=1.5, b=0.75 为默认值
# k1 控制词频饱和程度，b 控制文档长度归一化强度
# 【安装方式】pip install rank_bm25
from rank_bm25 import BM25Okapi

# List: 列表类型注解，用于标注参数和返回值类型
#   例如 List[str] 表示"字符串列表"，如 ["a", "b", "c"]
# Tuple: 元组类型注解，用于标注固定长度的复合类型
#   例如 Tuple[str, str] 表示"包含两个字符串的元组"，如 ("情景", "建议")
# 【什么是元组（tuple）？】类似列表，但不可修改。用圆括号定义：(1, 2, 3)
from typing import List, Tuple

# re: Python 正则表达式模块，用于文本分词（按单词边界分割）
# re.findall(模式, 文本) 找出文本中所有匹配模式的子串
import re


# =============================================================================
# FinancialSituationMemory 类 - 金融情景记忆系统
# =============================================================================
class FinancialSituationMemory:
    """
    金融情景记忆系统 - 使用 BM25 算法进行词法相似度匹配

    这个类实现了"记忆-检索"模式：
    1. 存储：将金融情景和对应的投资建议成对存入
    2. 索引：对存储的情景文本进行分词，建立 BM25 倒排索引
    3. 检索：根据当前情景，返回最相似的历史情景及其建议

    属性：
        name (str): 记忆实例的名称标识
        documents (List[str]): 存储的情景文本列表
        recommendations (List[str]): 存储的投资建议列表（与 documents 一一对应）
        bm25 (BM25Okapi | None): BM25 索引对象，None 表示尚未建立索引

    设计优势：
        - 纯本地运行，无需外部 API
        - 无 token 限制，可存储大量情景
        - 与 LLM 提供商无关，适用范围广
        - 基于词法的相似度，速度快，适合文本匹配场景
    """

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
        self.documents: List[str] = []
        # 投资建议列表（与 documents 一一对应）
        # documents[0] 的情景对应 recommendations[0] 的建议
        # 这两个列表通过索引位置保持对应关系
        self.recommendations: List[str] = []
        # BM25 索引对象，初始为空（还没有文档，无法建索引）
        # 添加文档后会通过 _rebuild_index() 创建 BM25Okapi 对象
        self.bm25 = None

    def _tokenize(self, text: str) -> List[str]:
        """
        对文本进行分词处理，供 BM25 索引使用

        分词策略：
        - 使用正则表达式 \\b\\w+\\b 匹配所有"单词"（连续字母数字字符）
        - 将所有文本转为小写（lowercase），保证大小写不敏感
        - 这个简单的分词策略适合英文金融文本

        参数：
            text (str): 待分词的原始文本

        返回：
            List[str]: 分词后的 token 列表，例如：
                      "Tech stocks rising!" -> ["tech", "stocks", "rising"]

        技术说明：
            - \\b 是单词边界断言，匹配单词开始或结束的位置
            - \\w+ 匹配一个或多个字母、数字或下划线
            - re.findall() 返回所有匹配的子串列表
        """
        # 将文本转为小写，然后使用正则表达式提取所有单词
        # text.lower() 把所有大写字母转成小写，保证 "Tech" 和 "tech" 被视为同一个词
        # re.findall(r'\b\w+\b', ...) 找出所有匹配 \b\w+\b 模式的子串
        #   \b = 单词边界（字母和非字母的交界处）
        #   \w+ = 一个或多个字母/数字/下划线
        # 例如："Tech stocks rising!" → lower → "tech stocks rising!"
        #   → findall → ["tech", "stocks", "rising"]
        # 注意感叹号 ! 不是 \w 字符，所以不会被匹配
        tokens = re.findall(r'\b\w+\b', text.lower())
        return tokens

    def _rebuild_index(self):
        """
        重建 BM25 索引

        当文档列表发生变化（添加新文档）后，需要调用此方法重建索引。
        BM25 索引是"全量"索引，不是增量索引，因此每次文档变化都需要完整重建。

        索引构建过程：
        1. 对所有文档进行分词
        2. 将分词后的文档列表传入 BM25Okapi 构造函数
        3. BM25Okapi 内部会计算文档频率（DF）和文档长度等统计信息

        特殊情况：
        - 如果文档列表为空，将 bm25 设为 None
        """
        # 检查文档列表是否非空
        # self.documents 是一个列表，if self.documents: 在列表非空时为真
        if self.documents:
            # 对每个文档进行分词，得到 token 列表的列表
            # 这是一个列表推导式：对 self.documents 中的每个 doc 调用 _tokenize
            # 例如：["High inflation...", "Tech sector..."]
            #   → [["high", "inflation", ...], ["tech", "sector", ...]]
            # 结果是一个"二维列表"——每个元素是一个文档的分词结果
            tokenized_docs = [self._tokenize(doc) for doc in self.documents]
            # 使用分词后的文档创建 BM25Okapi 索引对象
            # BM25Okapi 构造函数接收"分词后的文档列表"
            # 内部会计算：
            #   - 每个词在多少篇文档中出现（文档频率 DF）
            #   - 每篇文档的长度（词数）
            #   - 平均文档长度
            # 这些统计信息用于后续的相似度计算
            self.bm25 = BM25Okapi(tokenized_docs)
        else:
            # 如果没有文档，将索引设为 None
            # 没有文档就无法建立索引，get_memories 会检查这个条件
            self.bm25 = None

    def add_situations(self, situations_and_advice: List[Tuple[str, str]]):
        """
        添加金融情景及其对应的投资建议

        这是记忆系统的"写入"接口。每次调用都会将新的（情景，建议）对追加到
        现有记忆中，然后重建索引以包含新数据。

        参数：
            situations_and_advice (List[Tuple[str, str]]): 元组列表，
                每个元组包含两个元素：
                - 第一个元素 (str): 金融情景描述，如 "High inflation rate..."
                - 第二个元素 (str): 对应的投资建议，如 "Consider defensive sectors..."

        使用示例：
            memory.add_situations([
                ("High inflation rate", "Consider defensive sectors"),
                ("Tech sector volatility", "Reduce high-growth exposure"),
            ])

        注意事项：
            - documents 和 recommendations 两个列表通过索引位置一一对应
            - 例如 documents[0] 的情景对应 recommendations[0] 的建议
            - 每次添加后都会重建索引，频繁添加可能影响性能
        """
        # 遍历每个（情景，建议）元组
        # 【什么是元组拆包？】for situation, recommendation in ... 中，
        # 每个元素是 ("情景", "建议") 元组，Python 自动把它拆成两个变量
        for situation, recommendation in situations_and_advice:
            # 将情景文本追加到文档列表
            # .append() 是列表方法，在末尾添加一个元素
            self.documents.append(situation)
            # 将投资建议追加到建议列表（与文档列表保持索引对应）
            # 例如 documents[3] 对应 recommendations[3]
            self.recommendations.append(recommendation)

        # 文档列表已更新，重建 BM25 索引以包含新文档
        # 【为什么需要重建？】BM25 需要知道所有文档的统计信息（词频、文档长度等），
        # 添加新文档后这些信息变了，必须重新计算
        self._rebuild_index()

    def get_memories(self, current_situation: str, n_matches: int = 1) -> List[dict]:
        """
        根据当前情景检索最匹配的历史记忆

        这是记忆系统的"读取"接口。使用 BM25 算法计算当前情景与所有历史情景
        的相似度，返回最匹配的几条记录。

        检索流程：
        1. 对当前情景进行分词
        2. 使用 BM25 索引计算每条历史文档的得分
        3. 按得分降序排列，取前 n_matches 条
        4. 对得分进行归一化处理（0-1 范围）
        5. 返回包含匹配结果的结构化字典列表

        参数：
            current_situation (str): 当前的金融情景描述
            n_matches (int): 需要返回的匹配记录数量，默认为 1

        返回：
            List[dict]: 匹配结果列表，每个字典包含：
                - "matched_situation" (str): 匹配到的历史情景
                - "recommendation" (str): 对应的投资建议
                - "similarity_score" (float): 归一化后的相似度得分（0-1）

        边界情况：
            - 如果文档列表为空或索引为 None，返回空列表
        """
        # 边界检查：如果没有文档或索引未建立，直接返回空列表
        # not self.documents 在列表为空时为 True
        # self.bm25 is None 在索引未建立时为 True
        if not self.documents or self.bm25 is None:
            return []

        # 步骤1：对当前情景进行分词，得到查询 token 列表
        # 例如："Tech sector volatility" → ["tech", "sector", "volatility"]
        query_tokens = self._tokenize(current_situation)

        # 步骤2：使用 BM25 索引计算所有文档的得分
        # get_scores() 返回一个列表，长度等于文档数，每个元素是该文档与查询的相关性得分
        # 得分越高，表示文档与查询越相关
        # 例如：如果有 4 篇文档，scores 可能是 [3.5, 0.2, 1.1, 0.0]
        # 表示第一篇文档最相关（得分 3.5），第四篇最不相关（得分 0）
        scores = self.bm25.get_scores(query_tokens)

        # 步骤3：获取得分最高的前 n_matches 个文档的索引
        # 这一行比较复杂，逐步拆解：
        #
        # range(len(scores)) 生成所有文档的索引 [0, 1, 2, 3, ...]
        #
        # sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        # - sorted() 对索引列表排序
        # - key=lambda i: scores[i] 指定排序依据：每个索引 i 对应的得分 scores[i]
        #   【lambda 是什么？】lambda i: scores[i] 是一个匿名小函数，
        #   接收 i，返回 scores[i]。等价于 def f(i): return scores[i]
        # - reverse=True 表示降序排列（得分高的排在前面）
        # - 结果：按得分从高到低排列的文档索引列表，如 [0, 2, 1, 3]
        #
        # [:n_matches] 取前 n_matches 个
        # 例如 n_matches=2 时，取 [0, 2]（得分最高的两个文档的索引）
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n_matches]

        # 步骤4：构建结果列表
        results = []
        # 找到最高得分，用于归一化
        # max(scores) 返回得分列表中的最大值
        # 如果最高得分 <= 0，使用 1 作为分母（避免除以零）
        # 【为什么需要归一化？】BM25 的原始得分没有固定范围（可能是 0.5 或 12.3），
        # 归一化到 0-1 范围后便于理解和比较：1.0 表示最匹配，0 表示完全不匹配
        max_score = max(scores) if max(scores) > 0 else 1

        # 遍历每个最高得分的索引
        for idx in top_indices:
            # 步骤4a：计算归一化得分（0-1 范围）
            # 归一化公式：得分 / 最高得分
            # 例如：得分 3.5，最高得分 3.5 → 归一化得分 1.0（最匹配）
            #       得分 1.75，最高得分 3.5 → 归一化得分 0.5（一半匹配）
            # 归一化后便于比较和展示，1.0 表示最匹配
            normalized_score = scores[idx] / max_score if max_score > 0 else 0

            # 步骤4b：构建结果字典
            # 【什么是字典？】{键: 值} 的集合，如 {"name": "张三", "age": 25}
            # results.append(...) 把字典添加到结果列表中
            results.append({
                "matched_situation": self.documents[idx],       # 匹配到的历史情景
                "recommendation": self.recommendations[idx],    # 对应的投资建议
                "similarity_score": normalized_score,           # 归一化相似度得分
            })

        # 返回匹配结果列表
        return results

    def clear(self):
        """
        清空所有存储的记忆

        将所有内部状态重置为初始状态：
        - 文档列表清空
        - 建议列表清空
        - BM25 索引设为 None
        """
        # 清空文档列表（赋值为空列表）
        self.documents = []
        # 清空建议列表
        self.recommendations = []
        # 清空索引（设为 None 表示无索引）
        self.bm25 = None


# =============================================================================
# 模块自测代码（仅在直接运行此文件时执行）
# =============================================================================
# __name__ == "__main__" 是 Python 的惯用写法：
# - 当文件被直接运行时（python memory.py），__name__ 为 "__main__"，执行此代码块
# - 当文件被 import 导入时，__name__ 为模块名 "memory"，不执行此代码块
# 这段代码用于测试 FinancialSituationMemory 的功能是否正常
if __name__ == "__main__":
    # 创建一个记忆实例，名称为 "test_memory"
    # 这会调用 __init__ 方法，初始化空的文档列表和索引
    matcher = FinancialSituationMemory("test_memory")

    # 准备示例数据：四个（情景，建议）元组
    # 每个元组的第一个元素是市场情景描述，第二个元素是对应的投资建议
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
    # 这会触发 _rebuild_index()，建立 BM25 索引
    matcher.add_situations(example_data)

    # 准备一个查询情景（当前市场状况）
    # 这个情景与情景2（科技板块波动）有一些共同关键词
    current_situation = """
    Market showing increased volatility in tech sector, with institutional investors
    reducing positions and rising interest rates affecting growth stock valuations
    """

    try:
        # 检索最匹配的 2 条历史记忆
        # n_matches=2 表示返回得分最高的 2 条记录
        recommendations = matcher.get_memories(current_situation, n_matches=2)

        # 打印检索结果
        # enumerate(recommendations, 1) 遍历列表并从 1 开始编号
        # 【什么是 enumerate？】同时获取索引和元素的函数
        #   enumerate(["a", "b"], 1) → (1, "a"), (2, "b")
        for i, rec in enumerate(recommendations, 1):
            print(f"\nMatch {i}:")                                    # 匹配编号
            print(f"Similarity Score: {rec['similarity_score']:.2f}") # 相似度得分（保留两位小数）
            # 【:.2f 是什么？】f-string 的格式化语法，.2f 表示保留两位小数
            # 例如 0.856 → "0.86"
            print(f"Matched Situation: {rec['matched_situation']}")   # 匹配到的历史情景
            print(f"Recommendation: {rec['recommendation']}")         # 对应的投资建议

    except Exception as e:
        # 捕获并打印任何异常
        # 【什么是 Exception？】所有异常的基类，能捕获大多数运行时错误
        # str(e) 把异常对象转成字符串描述
        print(f"Error during recommendation: {str(e)}")
