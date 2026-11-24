"""源站权威序：同键多源冲突的权威序主链（独立链路，不动双轨对齐默认口径）。

形态（轨规 权威序形态 二选一）：
- 强源盖写：高级源到时盖写现行，被换下的弱源进只读归档；现行只读强源，读引用与归档不同表。
- 先到锁定：先到切流账册即锁定，后到（含强源）皆拒，除非人工解锁。

硬约束落法：
- 每笔事件 BEGIN IMMEDIATE 单事务：排序判定与弱源归档/拒绝同库同成败；不加锁会双成功。
- 盖写形态下强源后到必接管现行，双源并发弱源先落也留不住现行。
- 源登记与左轨同票：只认 源登记 轨别='左' 的源，校验与左装载同一套。
"""
from __future__ import annotations
import json, uuid
from app.db import 连接
from app.seed import 此刻
from app.services import 校验

形态可选 = ("强源盖写", "先到锁定")
默认形态 = "强源盖写"


def _形态(conn) -> str:
    r = conn.execute("SELECT 值 FROM 轨规 WHERE 键='权威序形态'").fetchone()
    v = (r["值"] if r else "") or 默认形态
    return v if v in 形态可选 else 默认形态


def _权威级(conn, 源键: str) -> int:
    r = conn.execute("SELECT 权威级 FROM 源权威 WHERE 源键=?", (源键,)).fetchone()
    return int(r["权威级"]) if r else 0


def _权威源(conn) -> str | None:
    """权威源=左轨登记源中权威级最高者（同级按源键定序，保证确定性）。"""
    r = conn.execute(
        """SELECT s.源键 AS 源键 FROM 源登记 s
           LEFT JOIN 源权威 q ON q.源键 = s.源键
           WHERE s.轨别='左'
           ORDER BY COALESCE(q.权威级,0) DESC, s.源键 LIMIT 1"""
    ).fetchone()
    return r["源键"] if r else None


def _左源已登记(conn, 源键: str) -> bool:
    return bool(
        conn.execute("SELECT 1 FROM 源登记 WHERE 源键=? AND 轨别='左'", (源键,)).fetchone()
    )


def 策略() -> dict:
    """策略列表：当前形态 + 左轨登记源及其权威级（对齐源登记页口径）。"""
    conn = 连接()
    try:
        源 = [
            {"源键": r["源键"], "名称": r["名称"], "说明": r["说明"], "权威级": int(r["级"])}
            for r in conn.execute(
                """SELECT s.源键, s.名称, s.说明, COALESCE(q.权威级,0) AS 级
                   FROM 源登记 s LEFT JOIN 源权威 q ON q.源键=s.源键
                   WHERE s.轨别='左' ORDER BY 级 DESC, s.源键"""
            ).fetchall()
        ]
        return {"形态": _形态(conn), "形态可选": list(形态可选), "权威源": _权威源(conn), "源": 源}
    finally:
        conn.close()


def 改策略(体: dict) -> dict:
    形态 = 体.get("形态")
    if 形态 is not None and 形态 not in 形态可选:
        return {"已改": False, "原因": f"形态须为 {'/'.join(形态可选)}"}
    conn = 连接()
    未知: list[str] = []
    try:
        if 形态 is not None:
            conn.execute(
                "INSERT INTO 轨规(键,值) VALUES('权威序形态',?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (形态,),
            )
            if 形态 == "先到锁定":
                # 切到先到锁定：现行账册全量落锁，后到皆拒，唯人工解锁可切流
                conn.execute("UPDATE 权威账册 SET 锁定=1")
        for 源键, 级 in (体.get("权威级") or {}).items():
            if not _左源已登记(conn, 源键):
                未知.append(源键)
                continue
            conn.execute(
                "INSERT INTO 源权威(源键,权威级) VALUES(?,?) ON CONFLICT(源键) DO UPDATE SET 权威级=excluded.权威级",
                (源键, int(级)),
            )
        conn.commit()
    finally:
        conn.close()
    out = 策略()
    out["已改"] = True
    if 未知:
        out["未知左源"] = 未知
    return out


def _落一笔(conn, 形态: str, 编号: str, 键: str, 金额: int, 源: str, 事件时: str) -> dict:
    """在 BEGIN IMMEDIATE 事务内处置一笔：排序判定与弱源命运同事务、同成败。"""
    时刻 = 此刻()
    if conn.execute("SELECT 1 FROM 权威账册 WHERE 现行事件编号=?", (编号,)).fetchone() or conn.execute(
        "SELECT 1 FROM 权威旁录 WHERE 事件编号=?", (编号,)
    ).fetchone():
        return {"命运": "幂等跳过", "说明": "事件编号已处置", "跳过": 1}
    级 = _权威级(conn, 源)
    row = conn.execute("SELECT * FROM 权威账册 WHERE 业务键=?", (键,)).fetchone()
    if row is None:
        锁定 = 1 if 形态 == "先到锁定" else 0
        conn.execute(
            """INSERT INTO 权威账册(业务键,现行事件编号,现行源键,现行金额分,现行权威级,事件时,形态,锁定,时刻)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (键, 编号, 源, 金额, 级, 事件时, 形态, 锁定, 时刻),
        )
        return {"命运": "现行-首到" + ("·已锁定" if 锁定 else ""), "说明": f"{源}(级{级})首到入现行", "入流": 1}
    if 形态 == "强源盖写":
        if 级 > int(row["现行权威级"]):
            # 强源盖写：弱源现行进只读归档，强源接管现行；两写同一事务
            conn.execute(
                """INSERT INTO 权威旁录(业务键,事件编号,源键,金额分,事件时,类别,原因,时刻)
                   VALUES (?,?,?,?,?,'归档',?,?)""",
                (键, row["现行事件编号"], row["现行源键"], row["现行金额分"], row["事件时"],
                 f"被强源盖写:{源}(级{级})>{row['现行源键']}(级{row['现行权威级']})", 时刻),
            )
            conn.execute(
                """UPDATE 权威账册 SET 现行事件编号=?,现行源键=?,现行金额分=?,现行权威级=?,事件时=?,形态=?,时刻=?
                   WHERE 业务键=?""",
                (编号, 源, 金额, 级, 事件时, 形态, 时刻, 键),
            )
            return {"命运": "现行-强源盖写", "说明": f"{源}(级{级})盖写，弱源{row['现行源键']}已归档", "入流": 1, "归档": 1}
        conn.execute(
            """INSERT INTO 权威旁录(业务键,事件编号,源键,金额分,事件时,类别,原因,时刻)
               VALUES (?,?,?,?,?,'归档',?,?)""",
            (键, 编号, 源, 金额, 事件时,
             f"弱源让位:{源}(级{级})≤现行{row['现行源键']}(级{row['现行权威级']})", 时刻),
        )
        return {"命运": "归档-弱源让位", "说明": f"现行仍为{row['现行源键']}", "归档": 1}
    # 先到锁定
    if int(row["锁定"]):
        conn.execute(
            """INSERT INTO 权威旁录(业务键,事件编号,源键,金额分,事件时,类别,原因,时刻)
               VALUES (?,?,?,?,?,'拒绝',?,?)""",
            (键, 编号, 源, 金额, 事件时,
             f"先到已锁定:现行{row['现行源键']}，后到{源}(级{级})亦拒，需人工解锁", 时刻),
        )
        return {"命运": "拒绝-先到锁定", "说明": f"现行{row['现行源键']}已锁定", "拒绝": 1}
    # 人工解锁后首笔：切流接管并再锁定
    conn.execute(
        """INSERT INTO 权威旁录(业务键,事件编号,源键,金额分,事件时,类别,原因,时刻)
           VALUES (?,?,?,?,?,'归档',?,?)""",
        (键, row["现行事件编号"], row["现行源键"], row["现行金额分"], row["事件时"],
         f"解锁后切流让位:{源}(级{级})接管", 时刻),
    )
    conn.execute(
        """UPDATE 权威账册 SET 现行事件编号=?,现行源键=?,现行金额分=?,现行权威级=?,事件时=?,形态=?,锁定=1,时刻=?
           WHERE 业务键=?""",
        (编号, 源, 金额, 级, 事件时, 形态, 时刻, 键),
    )
    return {"命运": "现行-解锁切流·已再锁定", "说明": f"{源}接管，旧现行{row['现行源键']}已归档", "入流": 1, "归档": 1}


def 采集(体: dict) -> dict:
    """多源采集入库的权威覆盖编排：校验→源登记同票→逐笔加锁落账册。"""
    批次 = 体.get("批次编号") or f"权批-{uuid.uuid4().hex[:8]}"
    事件 = 体.get("事件") or []
    报告 = {"批次编号": 批次, "轨别": "权威序", "形态": "",
            "入流": 0, "归档": 0, "拒绝": 0, "跳过": 0, "驳回": 0, "明细": []}
    for e in 校验.校验批次(体):
        报告["明细"].append({"命运": "批次预检", "说明": e})
    conn = 连接()
    conn.isolation_level = None  # 手动事务：BEGIN IMMEDIATE 串行化并发采集，不加锁会双成功
    try:
        形态 = _形态(conn)
        报告["形态"] = 形态
        for ev in 事件:
            行错 = 校验.校验事件(ev)
            if 行错:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-校验", "说明": ";".join(行错)})
                continue
            编号 = ev.get("事件编号")
            键 = ev.get("业务键")
            if not 编号 or not 键:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-缺字段", "事件": ev})
                continue
            源 = ev.get("源键") or ""
            if not 源 or not _左源已登记(conn, 源):
                报告["驳回"] += 1
                报告["明细"].append({"事件编号": 编号, "命运": "驳回-未知左源",
                                     "说明": f"源[{源 or '空'}]未在源登记左轨"})
                continue
            金额 = int(ev.get("金额分", 0))
            事件时 = ev.get("事件时") or 此刻()
            conn.execute("BEGIN IMMEDIATE")
            try:
                果 = _落一笔(conn, 形态, 编号, 键, 金额, 源, 事件时)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            for k in ("入流", "归档", "拒绝", "跳过"):
                报告[k] += 果.get(k, 0)
            报告["明细"].append({"事件编号": 编号, "业务键": 键, "源键": 源,
                                 "命运": 果["命运"], "说明": 果["说明"]})
        conn.execute(
            """INSERT INTO 装载批次(编号,轨别,时刻,报告) VALUES (?,?,?,?)
               ON CONFLICT(编号) DO UPDATE SET 时刻=excluded.时刻, 报告=excluded.报告""",
            (批次, "权威序", 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        return 报告
    finally:
        conn.close()


def 解锁(业务键: str) -> dict:
    """人工解锁：先到锁定形态下放行后到新源（解锁后首笔切流接管并再锁定）。"""
    if not 业务键:
        return {"解锁": False, "原因": "缺业务键"}
    conn = 连接()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM 权威账册 WHERE 业务键=?", (业务键,)).fetchone()
            if row is None:
                out = {"解锁": False, "业务键": 业务键, "原因": "无此业务键"}
            elif not int(row["锁定"]):
                out = {"解锁": False, "业务键": 业务键, "原因": "未锁定"}
            else:
                时刻 = 此刻()
                conn.execute("UPDATE 权威账册 SET 锁定=0, 时刻=? WHERE 业务键=?", (时刻, 业务键))
                conn.execute(
                    "INSERT INTO 权威旁录(业务键,类别,原因,时刻) VALUES (?,?,?,?)",
                    (业务键, "操作", f"人工解锁:放行后到新源，现行{row['现行源键']}待切流", 时刻),
                )
                out = {"解锁": True, "业务键": 业务键, "现行源": row["现行源键"]}
            conn.execute("COMMIT")
            return out
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def 权威序列表() -> dict:
    """权威序只读接口：业务键、权威源、现行源、归档或拒因、valid。

    valid = 现行源即权威源（强源是否已占现行，可区分强源后到前后的现行身份）。
    """
    conn = 连接()
    try:
        权威源 = _权威源(conn)
        行 = []
        for r in conn.execute("SELECT * FROM 权威账册 ORDER BY 业务键").fetchall():
            旁录 = [
                dict(p)
                for p in conn.execute(
                    "SELECT * FROM 权威旁录 WHERE 业务键=? ORDER BY 编号", (r["业务键"],)
                ).fetchall()
            ]
            行.append({
                "业务键": r["业务键"],
                "权威源": 权威源,
                "现行源": r["现行源键"],
                "现行事件编号": r["现行事件编号"],
                "现行金额分": r["现行金额分"],
                "现行权威级": r["现行权威级"],
                "valid": r["现行源键"] == 权威源,
                "锁定": bool(r["锁定"]),
                "形态": r["形态"],
                "事件时": r["事件时"],
                "时刻": r["时刻"],
                "归档或拒因": "；".join(f"{p['类别']}:{p['原因']}" for p in 旁录),
                "旁录": 旁录,
            })
        旁录全 = [
            dict(p)
            for p in conn.execute("SELECT * FROM 权威旁录 ORDER BY 编号 DESC").fetchall()
        ]
        return {"形态": _形态(conn), "权威源": 权威源, "行": 行, "旁录": 旁录全}
    finally:
        conn.close()
