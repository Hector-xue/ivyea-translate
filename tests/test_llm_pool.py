"""大模型连接池：按 base_url 复用、设置变更后重建、预热不抛。"""
from ivyea_translate import llm


def test_pool_reuses_client_per_base_url():
    a1 = llm._http_client("https://api.example-a.com/v1")
    a2 = llm._http_client("https://api.example-a.com/v1")
    b = llm._http_client("https://api.example-b.com/v1")
    assert a1 is a2          # 同地址复用（keep-alive 才有意义）
    assert a1 is not b       # 不同地址各一条池
    llm.reset_http_pool()


def test_reset_pool_closes_and_rebuilds():
    old = llm._http_client("https://api.example.com/v1")
    llm.reset_http_pool()
    assert old.is_closed     # 旧连接确实关掉，不泄漏
    new = llm._http_client("https://api.example.com/v1")
    assert new is not old
    llm.reset_http_pool()


def test_prewarm_never_raises():
    """预热是尽力而为：地址不可达/为空都不能炸。"""
    import time

    llm.prewarm_async("")                       # 空地址直接忽略
    llm.prewarm_async("https://127.0.0.1:1")    # 必然连不上，也不能抛
    time.sleep(0.1)                             # 让后台线程跑起来（异常会被吞掉）
    llm.reset_http_pool()
