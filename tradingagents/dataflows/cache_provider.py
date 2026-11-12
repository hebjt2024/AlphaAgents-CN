"""离线演示数据生成器（仅供离线演示与测试，禁止用于真实决策）。

⚠️ 重要说明
    本模块**不是缓存**，而是一个"离线演示 / 测试夹具（fixture）"生成器：
    它用随机游走生成价格、用固定模板生成财报与新闻，全部是**伪造数据**。

    历史上本模块被当作"缓存供应商"接入路由层，导致两个严重问题：
      1. 名字具有误导性（"cache" 听起来像"真实缓存"），维护者容易误以为
         返回的是真实历史数据；
      2. 路由层只能靠字符串嗅探（查找 "(cached)" / "simulated data" 等子串）
         来识别伪造数据，既会误杀真实数据，也会漏判真正的伪造数据。

    现在：
      - 所有对外函数都返回 SyntheticPayload（str 的子类，带显式标记），
        上层用 isinstance 判断即可精确识别，不再依赖脆弱的字符串匹配；
      - 本模块默认不会被路由层启用（需要显式设置 ALLOW_SYNTHETIC_DATA=1），
        该开关的语义是"允许使用离线演示数据"，绝不代表数据可信。

    允许用途：单元测试、离线演示、无网络环境下的流水线连通性验证。
    禁止用途：任何会产生真实投资决策结论的场景。
"""

import random
from datetime import datetime

# ============================================================================
# 合成数据标记
# ============================================================================
# WHY：路由层需要可靠地识别"这段数据是伪造的"。
# 早期实现靠字符串嗅探（"(cached)"、"simulated data for testing" 等），
# 问题是：真实数据正文里也可能出现这些子串（例如新闻里提到 cached），
# 从而被误判为伪造数据而丢弃。
# 现在改用显式类型标记：SyntheticPayload 是 str 的子类，
# 上层用 isinstance(result, SyntheticPayload) 精确判断。

# 合成数据标记常量（供外部引用/日志使用）
SYNTHETIC_MARKER = "SYNTHETIC"


class SyntheticPayload(str):
    """带有“合成数据”标记的字符串载荷。

    继承 str 是为了保持向后兼容：所有调用方都把它当普通字符串使用
    （startswith / split / 拼接等都不受影响），同时又能让上层通过
    isinstance 检查精确识别“这是离线演示数据，不是真实数据”。

    属性：
        is_synthetic: 恒为 True，标识该载荷来自离线演示数据生成器。
    """

    is_synthetic = True

    def __new__(cls, value: str = ""):
        obj = super().__new__(cls, value)
        return obj


def _synthetic(text: str) -> SyntheticPayload:
    """把生成的文本包装成带合成标记的载荷。"""
    return SyntheticPayload(text if isinstance(text, str) else str(text))


# ============================================================================
# 简易股票代码标准化（不依赖外部模块，避免导入链问题）
# ============================================================================

# 常见 A 股中文名称 → 6 位代码映射
_CHINESE_NAME_MAP = {
    "海南橡胶": "601118",
    "平安银行": "000001",
    "万科A": "000002",
    "万科": "000002",
    "中国平安": "601318",
    "贵州茅台": "600519",
    "宁德时代": "300750",
    "比亚迪": "002594",
    "招商银行": "600036",
    "五粮液": "000858",
    "京东方A": "000725",
    "京东方": "000725",
    "中兴通讯": "000063",
    "东方财富": "300059",
    "中芯国际": "688981",
    "工商银行": "601398",
    "农业银行": "601288",
    "中国石油": "601857",
    "中国石化": "600028",
    "长江电力": "600900",
    "中国神华": "601088",
    "隆基绿能": "601012",
    "海尔智家": "600690",
    "格力电器": "000651",
    "美的集团": "000333",
    "海康威视": "002415",
    "伊利股份": "600887",
    "恒瑞医药": "600276",
    "药明康德": "603259",
    "中信证券": "600030",
    "中国中车": "601766",
    "中国建筑": "601668",
    "中国联通": "600050",
    "紫金矿业": "601899",
    "万华化学": "600309",
    "三一重工": "600031",
    "福耀玻璃": "600660",
    "海螺水泥": "600585",
    "上汽集团": "600104",
    "保利发展": "600048",
    "通威股份": "600438",
    "阳光电源": "300274",
    "科大讯飞": "002230",
    "立讯精密": "002475",
    "工业富联": "601138",
}


def _normalize(symbol: str) -> str:
    """
    标准化股票代码：返回 6 位纯数字代码。

    支持格式：
      - 中文名 → 6 位代码（如 "海南橡胶" → "601118"）
      - sh.XXXXXX / sz.XXXXXX → 6 位代码
      - XXXXXX.SZ / XXXXXX.SS → 6 位代码（yfinance 后缀）
      - 纯 6 位数字 → 原样返回
    """
    s = symbol.strip()
    # 中文名 → 代码
    if s in _CHINESE_NAME_MAP:
        return _CHINESE_NAME_MAP[s]
    # 带前缀 → 6位代码
    if s.lower().startswith(("sh", "sz", "bj")) and len(s) == 8:
        return s[2:]
    # 带 .SZ / .SS / .SH 后缀（yfinance 格式）→ 6位代码
    if "." in s:
        parts = s.split(".")
        if parts[0].isdigit() and len(parts[0]) == 6:
            return parts[0]
    return s


# ============================================================================
# 预缓存的中国 A 股数据（真实数据快照）
# 格式：日期,开盘,收盘,最高,最低,成交量
# ============================================================================

# 平安银行 (000001) 2024年5月部分数据
_CACHED_STOCK_000001 = """
# Stock data for 000001 from 2024-05-01 to 2024-05-15 (cached)
# Total records: 7
# Data retrieved from cache

Date,Open,High,Low,Close,Volume
2024-05-06,10.85,10.88,10.72,10.80,88456700
2024-05-07,10.83,10.95,10.80,10.92,73689100
2024-05-08,10.90,10.90,10.72,10.78,91623400
2024-05-09,10.82,10.90,10.78,10.88,68234100
2024-05-10,10.89,10.95,10.82,10.94,77891200
2024-05-13,10.90,10.92,10.78,10.85,59123400
2024-05-14,10.87,10.95,10.82,10.93,64567800
2024-05-15,10.92,10.98,10.85,10.96,72345600
"""

# 海南橡胶 (601118) 2024年5月部分数据
_CACHED_STOCK_601118 = """
# Stock data for 601118 from 2024-05-01 to 2024-05-15 (cached)
# Total records: 7
# Data retrieved from cache

Date,Open,High,Low,Close,Volume
2024-05-06,5.08,5.15,5.05,5.12,45678900
2024-05-07,5.13,5.18,5.08,5.10,38765400
2024-05-08,5.09,5.11,5.02,5.05,42123400
2024-05-09,5.06,5.14,5.04,5.11,39876500
2024-05-10,5.12,5.20,5.10,5.18,51234500
2024-05-13,5.16,5.17,5.08,5.10,40234500
2024-05-14,5.11,5.18,5.12,5.16,38901200
2024-05-15,5.15,5.22,5.12,5.20,46789000
"""

# 缓存股票代码 → 缓存数据映射
_CACHED_STOCKS = {
    "000001": _CACHED_STOCK_000001,
    "601118": _CACHED_STOCK_601118,
}

# 为 A 股代码自动生成近似数据（基于最后一个缓存交易日的价格）
_GENERATED_CACHE: dict = {}


def _get_base_price_from_cache(code: str) -> float:
    """从预缓存数据中提取最后一个已知价格，用于生成数据的基础价格"""
    if code in _CACHED_STOCKS:
        cached = _CACHED_STOCKS[code]
        # 解析最后一行数据获取收盘价
        lines = cached.strip().split("\n")
        for line in reversed(lines):
            parts = line.split(",")
            if len(parts) >= 5 and parts[0].strip().startswith("202"):
                try:
                    return float(parts[4])  # Close 价格
                except (ValueError, IndexError):
                    pass
    return None


def _generate_stock_data(symbol: str, start_date: str, end_date: str, base_price: float = None) -> str:
    """基于代码特征生成合理的模拟股价数据"""
    try:
        from datetime import timedelta

        code = symbol.strip()
        # 对非 6 位数字代码（如中文名、美股代码），派生一个稳定的伪 6 位代码，
        # 保证模拟数据兜底始终可用（cache 本就是流水线最后保障）
        if not code.isdigit() or len(code) != 6:
            code = str(abs(hash(code)) % 1000000).zfill(6)

        # 优先使用预缓存数据中的价格，否则基于代码 hash 生成
        if base_price is None:
            cached_price = _get_base_price_from_cache(code)
            if cached_price is not None:
                base_price = cached_price
            else:
                base_price = float(hash(code) % 9500) / 100 + 5.0

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        lines = []
        current = start_dt
        count = 0
        prev_close = base_price
        while current <= end_dt:
            # 跳过周末
            if current.weekday() < 5:
                # 用随机种子确保同一个日期每次都生成相同价格
                random.seed(int(current.strftime("%Y%m%d")) + int(code))
                daily_change = (random.random() - 0.45) * 0.04  # -1.8% ~ +2.2%
                open_price = round(prev_close * (1 + daily_change * 0.5), 2)
                close_price = round(prev_close * (1 + daily_change), 2)
                high = round(max(open_price, close_price) * (1 + random.random() * 0.02), 2)
                low = round(min(open_price, close_price) * (1 - random.random() * 0.02), 2)
                volume = int(random.randint(10000000, 100000000))
                lines.append(f"{current.strftime('%Y-%m-%d')},{open_price},{high},{low},{close_price},{volume}")
                prev_close = close_price
                count += 1
            current = current + timedelta(days=1)

        random.seed()  # 重置随机种子

        if not lines:
            return ""

        header = f"# Stock data for {code} from {start_date} to {end_date} (generated)\n"
        header += f"# Total records: {count}\n"
        header += (
            "# WARNING: 离线演示数据（仅供离线演示与测试，禁止用于真实决策）"
            "/ simulated data for testing\n\n"
        )
        header += "Date,Open,High,Low,Close,Volume\n"

        return header + "\n".join(lines)

    except Exception:
        return ""


def get_cached_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """
    生成离线演示用的股票数据（**非真实行情**）。

    始终为请求的日期范围生成数据。对于已知的股票代码，
    使用预缓存数据中的最后一个价格作为生成数据的基础价格。

    参数：
      symbol: 股票代码
      start_date: 开始日期
      end_date: 结束日期

    返回：
      CSV 格式的 OHLCV 数据字符串（SyntheticPayload，带合成数据标记）
    """
    code = _normalize(symbol)

    # 始终为请求的日期范围生成数据
    # 预缓存数据仅用于提取基础价格，不直接返回
    if code.isdigit() and len(code) == 6:
        return _synthetic(_generate_stock_data(code, start_date, end_date))

    # 美股等，生成通用模拟数据
    return _synthetic(_generate_stock_data(code, start_date, end_date) or "")


def get_cached_fundamentals(ticker: str, curr_date: str = None) -> str:
    """生成离线演示用的基本面数据（**非真实财务数据**）。"""
    code = _normalize(ticker)

    # 预置海南橡胶的基本面数据
    if code == "601118":
        return _synthetic("""# Company Fundamentals for 601118 (cached)
# Data retrieved from cache

总市值: 18500000000
流通市值: 12000000000
行业: 橡胶和塑料制品业
市盈率-动态: 28.5
市净率: 1.85
每股收益: 0.18
每股净资产: 2.81
营业收入: 17850000000
净利润: 520000000
净利润同比增长率: 15.2%
毛利率: 12.8%
净利率: 2.9%
净资产收益率: 6.4%
资产负债率: 52.3%
总股本: 4280000000
流通股: 3200000000""")

    # 通用基本面数据
    return _synthetic(f"""# Company Fundamentals for {code} (generated)
# WARNING: 离线演示数据（仅供离线演示与测试，禁止用于真实决策）

总市值: 50000000000
流通市值: 35000000000
行业: 制造业
市盈率-动态: 15.0
市净率: 2.0
每股收益: 0.50
每股净资产: 3.50
营业收入: 25000000000
净利润: 1500000000
净资产收益率: 12.0
资产负债率: 45.0""")


def get_cached_indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """生成离线演示用的技术指标数据（**非真实指标**），基于演示价格生成模拟值"""
    from datetime import datetime, timedelta

    code = _normalize(symbol)

    # 先获取实际股价作为基准
    base_price = None
    try:
        start_dt = datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days + 10)
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        price_data = get_cached_stock_data(
            code, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
        )
        # 从CSV中解析最后一天的收盘价
        for line in reversed(price_data.split("\n")):
            if line and not line.startswith("#") and "," in line:
                parts = line.split(",")
                if len(parts) >= 5:
                    try:
                        base_price = float(parts[4])  # Close
                        break
                    except ValueError:
                        continue
    except Exception:
        pass

    # 若获取失败，用hash生成一个合理的价格区间（10-100）
    if base_price is None:
        base_price = (abs(hash(code)) % 9000) / 100 + 10.0

    result = f"## {indicator} values from cache:\n\n"
    current = datetime.strptime(curr_date, "%Y-%m-%d")
    random.seed(abs(hash(code + indicator)))  # 保证可重复

    for i in range(look_back_days, -1, -1):
        d = current - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        if d.weekday() >= 5:
            result += f"{date_str}: N/A: Not a trading day (weekend or holiday)\n"
        else:
            # 基于实际股价生成±15%的模拟波动
            noise = (random.random() - 0.5) * 0.3
            val = base_price * (1 + noise)
            result += f"{date_str}: {val:.4f}\n"

    result += "\n\n离线演示指标数据，仅供测试使用（禁止用于真实决策）。"
    return _synthetic(result)


def get_cached_news(ticker: str, start_date: str, end_date: str) -> str:
    """生成离线演示用的新闻数据（**非真实新闻**）"""
    code = _normalize(ticker)

    # 针对不同股票类型生成不同的新闻模板
    news_pool = [
        # 通用利好新闻
        (f"行业景气度回升，{code}业绩有望改善 (source: 证券时报)",
         f"最新行业数据显示，相关产业链景气度持续回升，{code}作为行业龙头，有望受益于下游需求恢复。"),
        # 业绩相关
        (f"{code}发布最新季度报告，营收稳步增长 (source: 东方财富)",
         "公司最新财报显示，营业收入同比增长8.5%，归母净利润同比增长12.3%，经营现金流持续改善。"),
        # 机构评级
        (f"券商给予{code}\"增持\"评级，目标价上调 (source: 中信证券)",
         "多家券商发布研报，看好公司长期发展前景，维持\"增持\"评级，部分机构上调目标价。"),
        # 政策利好
        (f"宏观政策利好，{code}所在行业迎来发展机遇 (source: 经济日报)",
         f"国务院常务会议提出加大相关领域支持力度，行业政策环境持续优化，利好{code}等龙头企业。"),
        # 市场表现
        (f"{code}近期股价表现活跃，换手率提升 (source: 同花顺)",
         f"近期{code}交易活跃度提升，日均换手率较前期增加，市场关注度提高。"),
        # 行业动态
        (f"行业整合加速，{code}竞争优势凸显 (source: 中国证券报)",
         f"行业竞争格局持续优化，龙头企业市场份额进一步提升，{code}凭借技术和规模优势占据有利位置。"),
        # 产能扩张
        (f"{code}产能扩张项目稳步推进 (source: 公司公告)",
         "公司新产能建设项目按计划推进，预计投产后将进一步提升公司盈利能力和市场竞争力。"),
        # 资金面
        (f"北向资金持续增持{code}，外资看好后市 (source: Wind数据)",
         f"沪深港通数据显示，近期北向资金持续净买入{code}，外资持股比例小幅上升。"),
    ]

    # 基于股票代码的 hash 选择 3 条新闻，确保同一股票每次返回相同新闻
    code_hash = hash(code) if code.isdigit() else hash(ticker)
    random.seed(code_hash)
    selected = random.sample(news_pool, min(3, len(news_pool)))
    random.seed()

    result = f"## {code} ({ticker}) News (cached), from {start_date} to {end_date}:\n\n"
    for title, body in selected:
        result += f"### {title}\n{body}\n\n"
    result += "\n注：以上为离线演示新闻数据，仅供测试使用（禁止用于真实决策）。实际新闻请以官方渠道为准。"

    return _synthetic(result)


def get_cached_global_news(curr_date: str, look_back_days: int = 7, limit: int = 10) -> str:
    """生成离线演示用的全球宏观新闻（**非真实新闻**）"""
    global_news_items = [
        ("央行维持LPR利率不变，货币政策保持稳健 (source: 中国人民银行)",
         "最新一期贷款市场报价利率（LPR）维持不变，1年期LPR为3.1%，5年期以上LPR为3.6%。央行表示将继续实施稳健的货币政策，保持流动性合理充裕。"),
        ("美联储维持利率不变，市场关注后续降息路径 (source: Bloomberg)",
         "美联储FOMC会议维持联邦基金利率目标区间不变，点阵图显示年内可能降息1-2次。鲍威尔表示将继续关注通胀和就业数据。"),
        ("A股三大指数震荡收涨，成交额突破万亿元 (source: 证券时报)",
         "沪指收涨0.3%，深成指涨0.5%，创业板指涨0.8%，两市成交额突破万亿元，北向资金净流入超50亿元。"),
        ("国际原油价格波动，OPEC+维持减产协议 (source: Reuters)",
         "布伦特原油期货价格在75-80美元区间震荡，OPEC+成员国同意维持现有减产协议至年底，市场关注全球需求变化。"),
        ("人民币汇率保持稳定，跨境资金流动平稳 (source: 外汇管理局)",
         "人民币对美元中间价报7.10，在岸人民币维持窄幅波动。外管局表示跨境资金流动总体平稳有序。"),
        ("国务院常务会议部署稳经济一揽子政策 (source: 新华社)",
         "会议提出加大宏观调控力度，扩大内需，提振市场信心，推动经济运行持续回升向好。"),
        ("全球半导体行业景气度回升，产业链迎机遇 (source: 电子信息产业网)",
         "全球半导体行业协会报告显示，2026年全球芯片销售额预计增长15%，AI芯片需求持续旺盛。"),
        ("房地产市场政策持续优化，多地推出购房补贴 (source: 住建部)",
         "多个城市推出购房补贴和税费减免政策，房地产市场活跃度有所回升，一线城市二手房成交量环比增长。"),
        ("新能源产业链投资持续增长 (source: 国家能源局)",
         "2026年上半年风电、光伏新增装机容量同比增长25%，新能源产业链投资保持高景气度。"),
        ("大宗商品价格走势分化，农产品价格温和上涨 (source: 期货日报)",
         "受全球气候和供需关系影响，部分农产品价格温和上涨，工业金属价格受需求预期影响维持震荡。"),
    ]

    random.seed(hash(curr_date))
    selected = random.sample(global_news_items, min(limit, len(global_news_items)))
    random.seed()

    result = f"## Global Market News (cached), last {look_back_days} days:\n\n"
    for title, body in selected:
        result += f"### {title}\n{body}\n\n"
    result += "\n注：以上为离线演示宏观新闻数据，仅供测试使用（禁止用于真实决策）。"

    return _synthetic(result)


def get_cached_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """生成离线演示用的资产负债表（**非真实财报**）"""
    code = _normalize(ticker)

    return _synthetic(f"""# Balance Sheet data for {code} ({freq}) (cached)
# WARNING: 离线演示数据（仅供离线演示与测试，禁止用于真实决策）

报告期,货币资金,应收账款,存货,固定资产,资产总计,短期借款,应付账款,负债合计,股东权益
2024-03-31,5200000000,3800000000,2800000000,15000000000,35000000000,3000000000,2500000000,18000000000,17000000000
2023-12-31,4800000000,3500000000,2600000000,14800000000,34000000000,2800000000,2400000000,17500000000,16500000000
2023-09-30,4500000000,3200000000,2500000000,14500000000,33000000000,2500000000,2200000000,17000000000,16000000000
""")


def get_cached_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """生成离线演示用的现金流量表（**非真实财报**）"""
    code = _normalize(ticker)

    return _synthetic(f"""# Cash Flow data for {code} ({freq}) (cached)
# WARNING: 离线演示数据（仅供离线演示与测试，禁止用于真实决策）

报告期,经营活动现金流,投资活动现金流,筹资活动现金流,现金净增加额
2024-03-31,850000000,-500000000,-200000000,150000000
2023-12-31,3200000000,-1800000000,-800000000,600000000
2023-09-30,2400000000,-1200000000,-500000000,700000000
""")


def get_cached_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """生成离线演示用的利润表（**非真实财报**）"""
    code = _normalize(ticker)

    return _synthetic(f"""# Income Statement data for {code} ({freq}) (cached)
# WARNING: 离线演示数据（仅供离线演示与测试，禁止用于真实决策）

报告期,营业收入,营业成本,销售费用,管理费用,研发费用,营业利润,利润总额,净利润
2024-03-31,4520000000,3800000000,180000000,120000000,80000000,580000000,560000000,520000000
2023-12-31,17850000000,15200000000,700000000,480000000,320000000,2200000000,2150000000,1980000000
2023-09-30,13200000000,11200000000,520000000,360000000,240000000,1650000000,1600000000,1480000000
""")


def get_cached_insider_transactions(ticker: str) -> str:
    """生成离线演示用的股东持股变动数据（**非真实数据**）"""
    code = _normalize(ticker)

    return _synthetic(f"""# Shareholder Holding Changes for {code} (cached)
# WARNING: 离线演示数据（仅供离线演示与测试，禁止用于真实决策）

股东名称,变动方向,变动数量,变动比例,变动日期
张三,增持,500000,0.012%,2024-05-10
李四,减持,200000,0.005%,2024-04-15""")
