"""EWMA、块自助法、离群点剔除（合成数据）。"""
import numpy as np
import pandas as pd
import pytest

from engine.model import block_bootstrap_indices, build_returns, ewma_filter, simulate_log_returns


def test_ewma_recursion_by_hand():
    r = np.array([[0.01], [-0.02], [0.03]])
    z, sig_next, sig_hist = ewma_filter(r, lam=0.9, init_n=2)
    v0 = (0.01 ** 2 + 0.02 ** 2) / 2
    v1 = 0.9 * v0 + 0.1 * 0.01 ** 2
    v2 = 0.9 * v1 + 0.1 * 0.02 ** 2
    v3 = 0.9 * v2 + 0.1 * 0.03 ** 2
    np.testing.assert_allclose(sig_hist[:, 0], np.sqrt([v0, v1, v2]))
    assert sig_next[0] == pytest.approx(np.sqrt(v3))
    raw = r[:, 0] / np.sqrt([v0, v1, v2])
    np.testing.assert_allclose(z[:, 0], raw - raw.mean())
    assert abs(z.mean()) < 1e-15                          # 去均值 → 零漂移


def test_bootstrap_blocks_are_contiguous_and_seeded():
    rng = np.random.default_rng(5)
    idx = block_bootstrap_indices(n_obs=100, n_paths=7, horizon=31, block_len=5, rng=rng)
    assert idx.shape == (7, 31)
    assert idx.min() >= 0 and idx.max() < 100
    for b in range(6):                                    # 每个块内部是连续日期
        blk = idx[:, b * 5:(b + 1) * 5]
        assert np.all(np.diff(blk, axis=1) == 1)
    again = block_bootstrap_indices(100, 7, 31, 5, np.random.default_rng(5))
    np.testing.assert_array_equal(idx, again)             # 固定种子可复现


def test_simulated_vol_follows_ewma():
    z = np.ones((50, 1))
    out = simulate_log_returns(z, np.array([0.01]), lam=0.94, n_paths=2, horizon=3, block_len=5,
                               rng=np.random.default_rng(0))
    v = 0.01 ** 2
    expected = []
    for _ in range(3):
        expected.append(np.sqrt(v))
        v = 0.94 * v + 0.06 * v                           # r² = σ² 时方差不变
    np.testing.assert_allclose(out[0, :, 0], expected)


def test_outlier_removed_only_for_bonds():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2023-05-04", periods=300)
    eq = pd.Series(4000 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))), index=idx)
    bd = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.0005, 300))), index=idx)
    bd.iloc[150:] *= 1.01                                 # 国债换月跳空 +1%
    eq.iloc[200:] *= 1.08                                 # 股指真实大涨 +8%：必须保留
    r, info = build_returns({"IF": eq, "T": bd}, ["IF", "T"], pd.Timestamp("2023-05-01").date(), ["T"], 6.0)
    removed_dates = {x["date"] for x in info["removed_outliers"]}
    assert idx[150].date().isoformat() in removed_dates
    assert idx[200] in r.index                            # 股指大涨日没被剔除
    assert idx[150] not in r.index
    assert info["n_returns"] == 299 - len(removed_dates)


def test_returns_use_date_intersection():
    a = pd.Series([1.0, 1.1, 1.2, 1.3], index=pd.to_datetime(["2023-05-04", "2023-05-05", "2023-05-08", "2023-05-09"]))
    b = pd.Series([2.0, 2.1, 2.2], index=pd.to_datetime(["2023-05-04", "2023-05-08", "2023-05-09"]))
    r, info = build_returns({"IF": a, "T": b}, ["IF", "T"], pd.Timestamp("2023-05-01").date(), ["T"], 1e9)
    assert info["n_prices"] == 3
    assert r.index[0] == pd.Timestamp("2023-05-08")
    assert r.loc["2023-05-08", "IF"] == pytest.approx(np.log(1.2 / 1.0))
