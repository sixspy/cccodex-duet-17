"""源站权威序：同键多源冲突权威序主链测试。

场景对照题面：
- 弱先强后·盖写形态 → 现行强源、弱源进只读归档
- 弱先强后·先到锁定形态 → 强源被拒，人工解锁后切流
- 仅强源 → 现行强
- 双源并发 → 弱源不留现行
- 源登记与左轨同票（未知源/右轨源皆驳回）
"""
import os
import threading
from pathlib import Path

_tmp = Path(__file__).resolve().parent / "_tmp"
_tmp.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_tmp)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app import seed
from app.modules.源站权威序 import 服务 as 权威序


def setup_function():
    db = _tmp / "对拍.db"
    if db.exists():
        db.unlink()
    seed.初始化库()


def _弱(编号, 键):
    return {"事件编号": 编号, "业务键": 键, "金额分": 100, "源键": "渠甲", "事件时": "2026-11-24T10:00:00Z"}


def _强(编号, 键):
    return {"事件编号": 编号, "业务键": 键, "金额分": 100, "源键": "渠乙", "事件时": "2026-11-24T10:00:01Z"}


def _行(键):
    for r in 权威序.权威序列表()["行"]:
        if r["业务键"] == 键:
            return r
    raise AssertionError(f"无账册行:{键}")


def test_弱先强后_盖写形态_现行强源弱归档():
    权威序.改策略({"形态": "强源盖写"})
    权威序.采集({"事件": [_弱("w1", "KG")]})
    行 = _行("KG")
    assert 行["现行源"] == "渠甲" and 行["valid"] is False  # 强源未到：现行非权威，可区分
    r = 权威序.采集({"事件": [_强("s1", "KG")]})
    assert r["入流"] == 1 and r["归档"] == 1
    行 = _行("KG")
    assert 行["现行源"] == "渠乙"          # 强源盖写后现行为强源
    assert 行["权威源"] == "渠乙"
    assert 行["valid"] is True
    assert "被强源盖写" in 行["归档或拒因"]  # 弱源进只读归档
    归档 = [p for p in 行["旁录"] if p["类别"] == "归档"]
    assert 归档 and 归档[0]["源键"] == "渠甲" and 归档[0]["事件编号"] == "w1"


def test_弱先强后_先到锁定_强源被拒_解锁后切流():
    权威序.改策略({"形态": "先到锁定"})
    r1 = 权威序.采集({"事件": [_弱("w1", "KL")]})
    assert r1["入流"] == 1
    行 = _行("KL")
    assert 行["现行源"] == "渠甲" and 行["锁定"] is True and 行["valid"] is False
    r2 = 权威序.采集({"事件": [_强("s1", "KL")]})
    assert r2["拒绝"] == 1                  # 先到锁定：后到强源亦拒
    行 = _行("KL")
    assert 行["现行源"] == "渠甲"
    assert "先到已锁定" in 行["归档或拒因"]
    assert 权威序.解锁("KL")["解锁"] is True  # 人工解锁
    r3 = 权威序.采集({"事件": [_强("s2", "KL")]})
    assert r3["入流"] == 1
    行 = _行("KL")
    assert 行["现行源"] == "渠乙" and 行["valid"] is True
    assert "切流让位" in 行["归档或拒因"]     # 旧现行弱源归档


def test_仅强源_现行强():
    权威序.改策略({"形态": "强源盖写"})
    权威序.采集({"事件": [_强("s1", "KS")]})
    行 = _行("KS")
    assert 行["现行源"] == "渠乙" and 行["valid"] is True


def test_双源并发_弱源不留现行():
    权威序.改策略({"形态": "强源盖写"})
    栅栏 = threading.Barrier(2)
    错 = []

    def 灌(ev):
        try:
            栅栏.wait(timeout=10)
            权威序.采集({"事件": [ev]})
        except Exception as e:  # noqa: BLE001
            错.append(e)

    t弱 = threading.Thread(target=灌, args=(_弱("cw1", "KC"),))
    t强 = threading.Thread(target=灌, args=(_强("cs1", "KC"),))
    t弱.start(); t强.start()
    t弱.join(15); t强.join(15)
    assert not 错
    行 = _行("KC")
    assert 行["现行源"] == "渠乙"            # 并发也不得弱源留现行
    assert 行["valid"] is True
    assert len(行["旁录"]) == 1              # 弱源只落归档/拒绝，不占现行


def test_源登记与左轨同票():
    r = 权威序.采集({"事件": [
        {"事件编号": "u1", "业务键": "KU", "金额分": 1, "源键": "鬼源", "事件时": "2026-11-24T10:00:00Z"},
        {"事件编号": "u2", "业务键": "KU", "金额分": 1, "源键": "核乙", "事件时": "2026-11-24T10:00:01Z"},
    ]})
    assert r["驳回"] == 2 and r["入流"] == 0  # 未登记源与右轨源皆驳回


def test_只读接口字段():
    权威序.采集({"事件": [_强("f1", "KF")]})
    d = 权威序.权威序列表()
    assert d["权威源"] == "渠乙"
    行 = _行("KF")
    for k in ("业务键", "权威源", "现行源", "归档或拒因", "valid"):
        assert k in 行


def test_幂等跳过():
    权威序.采集({"事件": [_强("m1", "KM")]})
    r = 权威序.采集({"事件": [_强("m1", "KM")]})
    assert r["跳过"] == 1 and r["入流"] == 0
