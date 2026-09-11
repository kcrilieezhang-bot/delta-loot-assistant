"""Offline sparse retrieval (Chinese bigrams + BM25), no embedding download or API."""
from __future__ import annotations

import math
import re
from collections import Counter

RULES = [
    ("rule:price", "价格与来源", "价格来自本地快照，不是实时成交价。缺价不能当作零元；"
     "手工总价优先，弹药总价为每发单价乘实际发数。"),
    ("rule:weapon", "整枪和配件", "整枪缩略图不能确认隐藏配件。枪体参考价不包含完整配置。"
     "需要配件详情或核实后的整枪总价，不能按枪体价建议丢枪。"),
    ("rule:keep", "保留与锁定", "锁定的物品不参与替换。安全箱中的物品默认保留。"
     "药品、装备耐久和钥匙次数可能影响实际成交价，目录参考价不等于残余价值。"),
    ("rule:swap", "拿取与替换", "优先查看地上每格价值较高的候选。背包内已定价、未锁定的"
     "低价物品可供比较；识别默认接受，无需逐项确认。收益差由计算工具得出，"
     "还需检查形状、空位及容器限制。"),
    ("rule:coverage", "漏识别与纠错", "全图分块放大补扫帮助读取小字。位置和数量仍可能错误，"
     "未知项不猜价格；框选、搜索并保存可以补项。看不见的物品不能推断为不存在。"),
    ("rule:scope", "全部物品排序", "全部范围包含背包、身上装备、口袋、胸挂、安全箱和地上"
     "已录入物品。全部物资合计不等于已经携带的撤离价值；未分区项单独标记。"),
]


def tokens(text):
    words = re.findall(r"[a-z0-9.]+|[\u4e00-\u9fff]+", text.casefold())
    return [token for word in words for token in
            ([word[i:i+2] for i in range(len(word)-1)] if len(word) > 1 and
             '\u4e00' <= word[0] <= '\u9fff' else [word])]


class KnowledgeRetriever:
    def __init__(self, snapshot):
        self.documents = [{"id": key, "title": title, "text": text,
                           "source": "项目估价规则 v1（非官方游戏规则）"}
                          for key, title, text in RULES]
        for definition in snapshot.catalog.items.values():
            item = snapshot.item(definition.id)
            self.documents.append({"id": "catalog:" + definition.id, "title": item["name"],
                                   "text": f"{item['name']}；类别 {item['category']}；"
                                   f"目录占格 {item['cells']}；快照单价 {item['unit_price']}。"
                                   "目录尺寸和特殊物品总价仍需人工核对。",
                                   "source": "本地目录/价格快照 " + str(
                                       snapshot.metadata()["generated_at"])})
        self.counts = [Counter(tokens(d["title"] + " " + d["text"])) for d in self.documents]
        self.df = Counter(t for count in self.counts for t in count)
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = sum(self.lengths) / max(1, len(self.lengths))

    def retrieve(self, question: str, limit=5):
        query = set(tokens(question))
        scored = []
        for index, count in enumerate(self.counts):
            score = 0.0
            for token in query & count.keys():
                idf = math.log(1 + (len(self.counts) - self.df[token] + .5) /
                               (self.df[token] + .5))
                tf = count[token]
                score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 *
                                         self.lengths[index] / max(1, self.average)))
            if score > 0:
                scored.append((score, index))
        return [{**self.documents[i], "score": round(score, 3)}
                for score, i in sorted(scored, reverse=True)[:limit]]
