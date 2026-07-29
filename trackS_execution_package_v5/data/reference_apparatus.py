#!/usr/bin/env python3
"""Canonical reference apparatus for queued-action dVOC model closure.

This file implements Branch B of the model-closure task.  It deliberately
separates:
  R: recovered facts from the supplied numerical artifacts,
  P: equations taken from primary literature,
  N: new reference-apparatus design decisions,
  V: quantities inferred and then numerically verified,
  U: evidence still unresolved for future certificate/hardware gates.

It closes a complete averaged hybrid model; it does NOT construct an invariant
set and does NOT claim recursive feasibility.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import numpy as np
from scipy.linalg import eigvals

Array = np.ndarray
J = np.array([[0.0, -1.0], [1.0, 0.0]])
I2 = np.eye(2)
SQRT3 = math.sqrt(3.0)
KAPPA_P = 1.5  # amplitude-invariant Clarke three-phase power factor

# Continuous/hybrid state order (32 real scalars)
STATE_ORDER = [
    "v_o_d", "v_o_q",                         # 0:2, V
    "i_f_d", "i_f_q",                         # 2:4, A
    "v_c_d", "v_c_q",                         # 4:6, V
    "i_g_d", "i_g_q",                         # 6:8, A
    "E_dc",                                      # 8, J
    "p_s",                                       # 9, W, source output power
    "E_s",                                       # 10, J, programmable-source buffer energy
    "p_r",                                       # 11, W, source recharge power
    "xi_v_d", "xi_v_q",                       # 12:14, V s
    "xi_i_d", "xi_i_q",                       # 14:16, A s
    "x_ad_d", "x_ad_q",                       # 16:18, A, LP capacitor-current state
    "hat_i_f_d", "hat_i_f_q",                 # 18:20, A
    "hat_v_c_d", "hat_v_c_q",                 # 20:22, V
    "hat_i_g_d", "hat_i_g_q",                 # 22:24, A
    "hat_v_g_d", "hat_v_g_q",                 # 24:26, V
    "hat_V_dc",                                  # 26, V
    "hat_p_s",                                   # 27, W
    "v_sw_hold_d", "v_sw_hold_q",             # 28:30, V
    "p_s_cmd_hold",                              # 30, W
    "p_r_cmd_hold",                              # 31, W
]
STATE_UNITS = [
    "V", "V", "A", "A", "V", "V", "A", "A", "J", "W", "J", "W",
    "V*s", "V*s", "A*s", "A*s", "A", "A",
    "A", "A", "V", "V", "A", "A", "V", "V", "V", "W", "V", "V", "W", "W",
]
ACTION_ORDER = ["sigma", "Delta_omega", "u_s"]
ACTION_UNITS = ["1/s", "rad/s", "W/s"]
# N: normal queued-action authority selected for the canonical apparatus.
ACTION_BOUNDS = {
    "absolute": {"sigma_1_s": 20.0, "Delta_omega_rad_s": 2.0 * math.pi * 2.0, "u_s_W_s": 1_000_000.0},
    "one_update_slew": {"sigma_1_s": 8.0, "Delta_omega_rad_s": 2.0 * math.pi * 0.8, "u_s_W_s": 500_000.0},
}
# N: declared smooth normal-domain template for Gate B1/B2. It is not yet a
# robust invariant or certified safe region.
NORMAL_DOMAIN_PU = {
    "v_o_magnitude": [0.85, 1.15],
    "V_dc": [0.90, 1.10],
    "i_f_magnitude": [0.0, 1.10],
    "i_g_magnitude": [0.0, 1.10],
    "modulation_norm": [0.0, 0.95],
}
MEASUREMENT_ORDER = [
    "i_f_d", "i_f_q", "v_c_d", "v_c_q", "i_g_d", "i_g_q",
    "v_g_d", "v_g_q", "V_dc", "p_s",
]

VO = slice(0, 2)
IF = slice(2, 4)
VC = slice(4, 6)
IG = slice(6, 8)
E_DC = 8
P_S = 9
E_S = 10
P_R = 11
XI_V = slice(12, 14)
XI_I = slice(14, 16)
X_AD = slice(16, 18)
YF = slice(18, 28)
VSW_HOLD = slice(28, 30)
PS_CMD_HOLD = 30
PR_CMD_HOLD = 31
N_X = len(STATE_ORDER)


@dataclass(frozen=True)
class Plant:
    S_b_VA: float = 100_000.0                 # R
    V_ll_rms_V: float = 400.0                  # R
    f_0_Hz: float = 50.0                       # R
    L_f_H: float = 0.0007639437268410976       # R
    C_f_F: float = 9.94718394324346e-05        # R
    L_g_H: float = 0.003305367739769311        # R
    R_f_ohm: float = 0.016                     # R
    R_g_ohm: float = 0.04784119008671966       # R
    V_dc_star_V: float = 900.0                 # R
    C_dc_F: float = 0.002                      # R
    T_outer_nom_s: float = 500e-6              # R
    # N: averaged loss model, declared rather than recovered
    p_loss_fixed_W: float = 150.0
    R_loss_equiv_ohm: float = 0.005

    @property
    def omega_0(self) -> float:
        return 2.0 * math.pi * self.f_0_Hz

    @property
    def I_phase_peak_A(self) -> float:
        return math.sqrt(2.0) * self.S_b_VA / (math.sqrt(3.0) * self.V_ll_rms_V)

    @property
    def V_phase_peak_V(self) -> float:
        return math.sqrt(2.0 / 3.0) * self.V_ll_rms_V

    @property
    def f_lcl_Hz(self) -> float:
        return math.sqrt((self.L_f_H + self.L_g_H) / (self.L_f_H * self.L_g_H * self.C_f_F)) / (2.0 * math.pi)


@dataclass(frozen=True)
class DVOC:
    # N: engineering targets for this 100-kVA reference apparatus
    full_power_droop_Hz: float = 0.5
    reactive_design_var: float = 50_000.0
    voltage_droop_pu: float = 0.05
    v_min_pu: float = 0.85


@dataclass(frozen=True)
class Controller:
    # N: nested PI / active damping design
    f_voltage_Hz: float = 150.0
    zeta_voltage: float = 0.9
    f_current_Hz: float = 1000.0
    zeta_current: float = 0.9
    f_active_damping_Hz: float = 300.0
    k_active_damping_ohm: float = 1.0
    T_aw_voltage_s: float = 1.0e-3
    T_aw_current_s: float = 0.2e-3
    current_reference_pu: float = 1.10
    # N: DC energy/source nominal action
    f_energy_Hz: float = 10.0
    zeta_energy: float = 0.9


@dataclass(frozen=True)
class Measurement:
    # N: 10 scalar first-order channels
    f_ac_filter_Hz: float = 5000.0
    f_dc_filter_Hz: float = 1000.0
    T_fast_sample_s: float = 50e-6
    acquisition_delay_s: float = 2e-6
    adc_transform_delay_s: float = 8e-6
    adc_bits: int = 16
    current_full_scale_A: float = 300.0
    ac_voltage_full_scale_V: float = 600.0
    dc_voltage_full_scale_V: float = 1200.0
    source_power_full_scale_W: float = 120_000.0
    current_noise_bound_A: float = 0.20
    ac_voltage_noise_bound_V: float = 0.25
    dc_voltage_noise_bound_V: float = 0.50
    source_power_noise_bound_W: float = 100.0


@dataclass(frozen=True)
class Modulator:
    # N; relation is standard SVPWM physics, numeric margins are design decisions
    f_pwm_Hz: float = 20_000.0
    m_max: float = 0.95
    dead_time_s: float = 2e-6


@dataclass(frozen=True)
class Source:
    # N: utility-backed bidirectional programmable DC source with finite buffer
    tau_s: float = 5e-3
    p_s_min_W: float = -50_000.0
    p_s_max_W: float = 100_000.0
    u_s_max_W_s: float = 1_000_000.0
    d_s_bound_W: float = 250.0  # Gate-B1 requirement, not yet hardware evidence
    C_buffer_F: float = 0.020
    V_buffer_star_V: float = 800.0
    V_buffer_min_V: float = 700.0
    V_buffer_max_V: float = 900.0
    I_buffer_max_A: float = 160.0
    p_source_loss_fixed_W: float = 100.0
    R_source_loss_ohm: float = 0.020
    tau_recharge_s: float = 2e-3
    p_recharge_min_W: float = -50_000.0
    p_recharge_max_W: float = 120_000.0
    k_buffer_energy_per_s: float = 30.0
    T_source_update_s: float = 100e-6

    @property
    def E_buffer_star_J(self) -> float:
        return 0.5 * self.C_buffer_F * self.V_buffer_star_V**2


@dataclass(frozen=True)
class Guard:
    # N: separate fast device-current owner
    T_guard_s: float = 50e-6
    I_warning_pu: float = 1.10
    I_limit_pu: float = 1.20
    I_release_pu: float = 1.05
    I_trip_pu: float = 1.35
    lambda_per_s: float = 4000.0
    max_intervention_V: float = 100.0
    voltage_slew_V_per_s: float = 5_000_000.0
    release_dwell_s: float = 0.5e-3


@dataclass(frozen=True)
class Timing:
    T_fast_s: float = 50e-6
    T_source_s: float = 100e-6
    T_outer_nom_s: float = 500e-6
    T_outer_min_s: float = 495e-6
    T_outer_max_s: float = 505e-6
    outer_compute_budget_s: float = 100e-6
    communication_delay_s: float = 10e-6


@dataclass(frozen=True)
class Apparatus:
    plant: Plant
    dvoc: DVOC
    controller: Controller
    measurement: Measurement
    modulator: Modulator
    source: Source
    guard: Guard
    timing: Timing
    # derived/recovered operating point
    x_star: tuple[float, ...]
    q_star: tuple[float, ...]
    v_g_star: tuple[float, float]
    v_sw_star: tuple[float, float]
    P_pcc_star_W: float
    Q_pcc_star_var: float
    P_bridge_star_W: float
    p_converter_loss_star_W: float
    p_s_star_W: float
    p_source_loss_star_W: float
    p_r_star_W: float
    # dVOC/controller gains
    V_star_V: float
    P_star_W: float
    Q_star_var: float
    kappa_rad: float
    eta_ohm_per_s: float
    alpha_S: float
    K_Pv_A_per_V: float
    K_Iv_A_per_Vs: float
    K_Pi_ohm: float
    K_Ii_ohm_per_s: float
    omega_ad: float
    I_ref_max_A: float
    k_E_per_s2: float
    k_P_per_s: float


@dataclass
class HybridMode:
    guard_active: bool = False
    release_timer_s: float = 0.0
    hardware_trip: bool = False


PRIMARY_REFERENCES = {
    "dvoc": {
        "authors": "G.-S. Seo, M. Colombino, I. Subotic, B. Johnson, D. Gross, and F. Dorfler",
        "title": "Dispatchable Virtual Oscillator Control for Decentralized Inverter-Dominated Power Systems: Analysis and Experiments",
        "venue": "IEEE Applied Power Electronics Conference and Exposition (APEC)",
        "year": 2019,
        "doi": "10.1109/APEC.2019.8722028",
        "classification": "P",
    },
    "nested_current_limiting": {
        "authors": "O. Ajala, M. Lu, B. B. Johnson, S. V. Dhople, and A. D. Dominguez-Garcia",
        "title": "Model Reduction for Inverters With Current Limiting and Dispatchable Virtual Oscillator Control",
        "venue": "IEEE Transactions on Energy Conversion",
        "year": 2022,
        "doi": "10.1109/TEC.2021.3083488",
        "classification": "P",
    },
    "active_damping": {
        "authors": "J. Dannehl, F. W. Fuchs, S. Hansen, and P. B. Thogersen",
        "title": "Investigation of Active Damping Approaches for PI-Based Current Control of Grid-Connected PWM Converters With LCL Filters",
        "venue": "IEEE Transactions on Industry Applications",
        "year": 2010,
        "doi": "10.1109/TIA.2010.2049974",
        "classification": "P",
    },
    "svpwm": {
        "authors": "H. W. van der Broeck, H.-C. Skudelny, and G. V. Stanke",
        "title": "Analysis and Realization of a Pulsewidth Modulator Based on Voltage Space Vectors",
        "venue": "IEEE Transactions on Industry Applications",
        "year": 1988,
        "doi": "10.1109/28.87265",
        "classification": "P",
    },
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clarke_amplitude_invariant() -> Array:
    return (2.0 / 3.0) * np.array(
        [[1.0, -0.5, -0.5], [0.0, math.sqrt(3.0) / 2.0, -math.sqrt(3.0) / 2.0]]
    )


def rotation(theta: float) -> Array:
    return np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])


def project_circle(v: Array, radius: float) -> Array:
    v = np.asarray(v, dtype=float).reshape(2)
    n = float(np.linalg.norm(v))
    if n <= radius or n == 0.0:
        return v.copy()
    return v * (radius / n)


def project_halfspace(v: Array, a: Array, b: float) -> Array:
    v = np.asarray(v, dtype=float).reshape(2)
    a = np.asarray(a, dtype=float).reshape(2)
    aa = float(a @ a)
    if aa <= 1e-20 or float(a @ v) <= b:
        return v.copy()
    return v - ((float(a @ v) - b) / aa) * a


def finite_difference_jacobian(fun: Callable[[Array], Array], x: Array, rel_step: float = 2e-6) -> Array:
    x = np.asarray(x, dtype=float)
    y0 = np.asarray(fun(x), dtype=float)
    Jfd = np.zeros((y0.size, x.size))
    for j in range(x.size):
        h = rel_step * max(1.0, abs(float(x[j])))
        xp, xm = x.copy(), x.copy()
        xp[j] += h
        xm[j] -= h
        Jfd[:, j] = (np.asarray(fun(xp)) - np.asarray(fun(xm))) / (2.0 * h)
    return Jfd


def eig_records(vals: Array) -> list[dict[str, float]]:
    out = []
    for z in vals:
        mag = abs(z)
        out.append({
            "real": float(np.real(z)),
            "imag": float(np.imag(z)),
            "magnitude": float(mag),
            "frequency_Hz": float(abs(np.imag(z)) / (2.0 * math.pi)),
            "damping_ratio": float(-np.real(z) / mag) if mag > 0 else 0.0,
        })
    return out


def loss_converter(i_f: Array, p: Plant) -> float:
    return p.p_loss_fixed_W + KAPPA_P * p.R_loss_equiv_ohm * float(i_f @ i_f)


def grad_loss_converter(i_f: Array, p: Plant) -> Array:
    return 2.0 * KAPPA_P * p.R_loss_equiv_ohm * np.asarray(i_f, dtype=float)


def Vdc_from_energy(E: float, p: Plant) -> float:
    return math.sqrt(max(1e-16, 2.0 * float(E) / p.C_dc_F))


def Vsource_from_energy(E: float, s: Source) -> float:
    return math.sqrt(max(1e-16, 2.0 * float(E) / s.C_buffer_F))


def loss_source(ps: float, Es: float, s: Source) -> float:
    Vs2 = max(1e-12, 2.0 * float(Es) / s.C_buffer_F)
    return s.p_source_loss_fixed_W + s.R_source_loss_ohm * float(ps) ** 2 / Vs2


def loss_source_grad(ps: float, Es: float, s: Source) -> tuple[float, float]:
    # loss = p0 + R * ps^2 * C / (2 Es)
    dps = s.R_source_loss_ohm * s.C_buffer_F * float(ps) / float(Es)
    dEs = -s.R_source_loss_ohm * s.C_buffer_F * float(ps) ** 2 / (2.0 * float(Es) ** 2)
    return dps, dEs


def build_apparatus(bundle: Path) -> tuple[Apparatus, Array, Array, Array]:
    raw = np.load(bundle)
    eq = raw["eq"].astype(float)
    old_Nv = raw["Nv"].astype(float)
    old_Ke = raw["Ke"].astype(float)
    old_A = raw["A"].astype(float)

    pp = Plant()
    dd = DVOC()
    cc = Controller()
    mm = Measurement()
    mod = Modulator()
    ss = Source()
    gg = Guard()
    tt = Timing()

    vo = eq[0:2]
    i_f = eq[2:4]
    v_c = eq[4:6]
    i_g = eq[6:8]
    E_dc = float(eq[8])
    v_sw = v_c + pp.R_f_ohm * i_f + pp.omega_0 * pp.L_f_H * (J @ i_f)
    v_g = v_c - pp.R_g_ohm * i_g - pp.omega_0 * pp.L_g_H * (J @ i_g)
    P_pcc = KAPPA_P * float(v_c @ i_g)
    Q_pcc = KAPPA_P * float(v_c @ (J @ i_g))
    P_bridge = KAPPA_P * float(v_sw @ i_f)
    pconv = loss_converter(i_f, pp)
    ps = P_bridge + pconv
    Es = ss.E_buffer_star_J
    psrc_loss = loss_source(ps, Es, ss)
    pr = ps + psrc_loss

    Vstar = float(np.linalg.norm(vo))
    kappa = math.atan2(pp.omega_0 * pp.L_g_H, pp.R_g_ohm)
    eta = 2.0 * math.pi * dd.full_power_droop_Hz * Vstar**2 / (pp.S_b_VA * math.sin(kappa))
    # Exact local radial linearization at the nonzero P*,Q* equilibrium:
    # dV/dQ = sin(kappa)*V / (2*(A_star-alpha*V^2)),
    # A_star = cos(kappa)P* + sin(kappa)Q*.  Enforce
    # dV/dQ = -(voltage_droop_pu*V)/reactive_design_var.
    A_star = math.cos(kappa) * P_pcc + math.sin(kappa) * Q_pcc
    alpha = (A_star + math.sin(kappa) * dd.reactive_design_var / (2.0 * dd.voltage_droop_pu)) / Vstar**2

    wv = 2.0 * math.pi * cc.f_voltage_Hz
    wi = 2.0 * math.pi * cc.f_current_Hz
    Kpv = 2.0 * cc.zeta_voltage * wv * pp.C_f_F
    Kiv = wv**2 * pp.C_f_F
    Kpi = 2.0 * cc.zeta_current * wi * pp.L_f_H
    Kii = wi**2 * pp.L_f_H
    wad = 2.0 * math.pi * cc.f_active_damping_Hz
    Iref = cc.current_reference_pu * pp.I_phase_peak_A
    wE = 2.0 * math.pi * cc.f_energy_Hz
    kP = 2.0 * cc.zeta_energy * wE
    kE = wE**2

    x = np.zeros(N_X)
    x[VO] = vo
    x[IF] = i_f
    x[VC] = v_c
    x[IG] = i_g
    x[E_DC] = E_dc
    x[P_S] = ps
    x[E_S] = Es
    x[P_R] = pr
    x[XI_V] = 0.0
    x[XI_I] = 0.0
    x[X_AD] = i_f - i_g
    x[YF] = np.r_[i_f, v_c, i_g, v_g, pp.V_dc_star_V, ps]
    x[VSW_HOLD] = v_sw
    x[PS_CMD_HOLD] = ps
    x[PR_CMD_HOLD] = pr

    app = Apparatus(
        plant=pp, dvoc=dd, controller=cc, measurement=mm, modulator=mod,
        source=ss, guard=gg, timing=tt,
        x_star=tuple(x.tolist()), q_star=(0.0, 0.0, 0.0),
        v_g_star=tuple(v_g.tolist()), v_sw_star=tuple(v_sw.tolist()),
        P_pcc_star_W=P_pcc, Q_pcc_star_var=Q_pcc, P_bridge_star_W=P_bridge,
        p_converter_loss_star_W=pconv, p_s_star_W=ps,
        p_source_loss_star_W=psrc_loss, p_r_star_W=pr,
        V_star_V=Vstar, P_star_W=P_pcc, Q_star_var=Q_pcc,
        kappa_rad=kappa, eta_ohm_per_s=eta, alpha_S=alpha,
        K_Pv_A_per_V=Kpv, K_Iv_A_per_Vs=Kiv,
        K_Pi_ohm=Kpi, K_Ii_ohm_per_s=Kii,
        omega_ad=wad, I_ref_max_A=Iref,
        k_E_per_s2=kE, k_P_per_s=kP,
    )
    return app, old_Nv, old_Ke, old_A


def measurement_vector(x: Array, app: Apparatus, v_g: Array | None = None) -> Array:
    vg = np.asarray(app.v_g_star if v_g is None else v_g, dtype=float)
    return np.r_[x[IF], x[VC], x[IG], vg, Vdc_from_energy(x[E_DC], app.plant), x[P_S]]


def dvoc_relative_field(v_o: Array, i_g_hat: Array, app: Apparatus) -> Array:
    Rk = rotation(app.kappa_rad)
    M = np.array([[app.P_star_W, app.Q_star_var], [-app.Q_star_var, app.P_star_W]])
    K = (Rk @ M) / app.V_star_V**2
    phi = (app.V_star_V**2 - float(v_o @ v_o)) / app.V_star_V**2
    i_o = KAPPA_P * np.asarray(i_g_hat, dtype=float)
    return app.eta_ohm_per_s * (K @ v_o - Rk @ i_o + app.alpha_S * phi * v_o)


def intrinsic_action(v_o: Array, relative_field: Array, app: Apparatus) -> Array:
    n2 = float(v_o @ v_o)
    vmin = app.dvoc.v_min_pu * app.V_star_V
    if math.sqrt(n2) < vmin:
        raise ValueError("intrinsic-coordinate domain violated")
    B = np.column_stack((v_o, J @ v_o))
    return (B.T @ relative_field) / n2


def nominal_action(x: Array, app: Apparatus) -> Array:
    y = x[YF]
    i_f_hat, i_g_hat = y[0:2], y[4:6]
    field = dvoc_relative_field(x[VO], i_g_hat, app)
    xi = intrinsic_action(x[VO], field, app)
    Ehat = 0.5 * app.plant.C_dc_F * float(y[8]) ** 2
    pbridge = KAPPA_P * float(x[VSW_HOLD] @ i_f_hat)
    ploss = loss_converter(i_f_hat, app.plant)
    p_load = pbridge + ploss
    us = -app.k_P_per_s * (float(y[9]) - p_load) - app.k_E_per_s2 * (Ehat - app.x_star[E_DC])
    us = float(np.clip(us, -app.source.u_s_max_W_s, app.source.u_s_max_W_s))
    return np.r_[xi, us]


def modulation_radius(Vdc: float, app: Apparatus) -> float:
    return app.modulator.m_max * float(Vdc) / SQRT3


def current_guard(v_request: Array, x: Array, previous_v: Array, mode: HybridMode, dt: float, app: Apparatus) -> tuple[Array, HybridMode, dict[str, float]]:
    g = app.guard
    pp = app.plant
    i = x[YF][0:2]
    vc = x[YF][2:4]
    Inom = pp.I_phase_peak_A
    Iwarn, Ilim, Irel, Itrip = g.I_warning_pu*Inom, g.I_limit_pu*Inom, g.I_release_pu*Inom, g.I_trip_pu*Inom
    ni = float(np.linalg.norm(i))
    new = HybridMode(mode.guard_active, mode.release_timer_s, mode.hardware_trip)
    if ni >= Itrip:
        new.hardware_trip = True
    if ni >= Iwarn:
        new.guard_active = True
        new.release_timer_s = 0.0
    elif new.guard_active:
        if ni <= Irel:
            new.release_timer_s += dt
            if new.release_timer_s >= g.release_dwell_s:
                new.guard_active = False
                new.release_timer_s = 0.0
        else:
            new.release_timer_s = 0.0

    v = np.asarray(v_request, dtype=float).copy()
    intervention = 0.0
    if new.guard_active and not new.hardware_trip:
        h = Ilim**2 - ni**2
        a = 2.0 * i
        b = 2.0 * float(i @ (vc + pp.R_f_ohm*i + pp.omega_0*pp.L_f_H*(J@i))) + pp.L_f_H*g.lambda_per_s*h
        projected = project_halfspace(v, a, b)
        delta = projected - v
        nd = float(np.linalg.norm(delta))
        if nd > g.max_intervention_V:
            projected = v + delta * (g.max_intervention_V / nd)
            new.hardware_trip = True
        v = projected
        intervention = float(np.linalg.norm(v - v_request))

    slew = g.voltage_slew_V_per_s * dt
    dv = v - previous_v
    ndv = float(np.linalg.norm(dv))
    if ndv > slew:
        v = previous_v + dv * (slew / ndv)
    return v, new, {"i_norm_A": ni, "intervention_V": intervention, "active": float(new.guard_active), "trip": float(new.hardware_trip)}


def controller_algebra(x: Array, app: Apparatus) -> dict[str, Array | float]:
    y = x[YF]
    i_f_hat, v_c_hat, i_g_hat = y[0:2], y[2:4], y[4:6]
    e_v = x[VO] - v_c_hat
    i_raw = i_g_hat + app.plant.omega_0*app.plant.C_f_F*(J@v_c_hat) + app.K_Pv_A_per_V*e_v + app.K_Iv_A_per_Vs*x[XI_V]
    i_ref = project_circle(i_raw, app.I_ref_max_A)
    e_i = i_ref - i_f_hat
    i_c_hp = (i_f_hat - i_g_hat) - x[X_AD]
    v_raw = v_c_hat + app.plant.R_f_ohm*i_f_hat + app.plant.omega_0*app.plant.L_f_H*(J@i_f_hat) + app.K_Pi_ohm*e_i + app.K_Ii_ohm_per_s*x[XI_I] - app.controller.k_active_damping_ohm*i_c_hp
    return {"e_v": e_v, "i_raw": i_raw, "i_ref": i_ref, "e_i": e_i, "i_c_hp": i_c_hp, "v_raw": v_raw}


def source_commands(x: Array, q: Array, app: Apparatus) -> tuple[float, float, dict[str, float]]:
    s = app.source
    ps, Es = float(x[P_S]), float(x[E_S])
    Vs = Vsource_from_energy(Es, s)
    current_limit_power = s.I_buffer_max_A * Vs
    pmin = max(s.p_s_min_W, -current_limit_power)
    pmax = min(s.p_s_max_W, current_limit_power)
    ps_cmd = float(np.clip(ps + s.tau_s * float(q[2]), pmin, pmax))
    ls = loss_source(ps, Es, s)
    pr_cmd = float(np.clip(ps + ls + s.k_buffer_energy_per_s*(s.E_buffer_star_J-Es), s.p_recharge_min_W, s.p_recharge_max_W))
    return ps_cmd, pr_cmd, {"V_source_V": Vs, "I_source_A": ps/max(Vs, 1e-9), "pmin_W": pmin, "pmax_W": pmax, "source_loss_W": ls}


def fast_event(x: Array, q: Array, mode: HybridMode, app: Apparatus) -> tuple[Array, HybridMode, dict[str, float]]:
    dt = app.timing.T_fast_s
    xx = x.copy()
    old_y = xx[YF].copy()
    alg = controller_algebra(xx, app)
    Vdc_hat = float(old_y[8])
    v_circle = project_circle(np.asarray(alg["v_raw"]), modulation_radius(Vdc_hat, app))
    v_guard, mode2, gd = current_guard(v_circle, xx, xx[VSW_HOLD], mode, dt, app)
    v_applied = project_circle(v_guard, modulation_radius(Vdc_hat, app))

    # Back-calculation anti-windup and active-damping state.
    e_v = np.asarray(alg["e_v"])
    i_raw = np.asarray(alg["i_raw"])
    i_ref = np.asarray(alg["i_ref"])
    e_i = np.asarray(alg["e_i"])
    v_raw = np.asarray(alg["v_raw"])
    xx[XI_V] += dt * (e_v + (i_ref-i_raw)/(app.K_Iv_A_per_Vs*app.controller.T_aw_voltage_s))
    xx[XI_I] += dt * (e_i + (v_applied-v_raw)/(app.K_Ii_ohm_per_s*app.controller.T_aw_current_s))
    i_c_meas = old_y[0:2] - old_y[4:6]
    a_ad = math.exp(-app.omega_ad*dt)
    xx[X_AD] = a_ad*xx[X_AD] + (1.0-a_ad)*i_c_meas
    xx[VSW_HOLD] = v_applied

    # Controller uses old filtered sample; filter then receives the new sample.
    y_meas = measurement_vector(xx, app)
    a = np.r_[np.full(8, math.exp(-2*math.pi*app.measurement.f_ac_filter_Hz*dt)), np.full(2, math.exp(-2*math.pi*app.measurement.f_dc_filter_Hz*dt))]
    xx[YF] = a*old_y + (1.0-a)*y_meas
    return xx, mode2, gd


def source_event(x: Array, q: Array, app: Apparatus) -> tuple[Array, dict[str, float]]:
    xx = x.copy()
    ps_cmd, pr_cmd, info = source_commands(xx, q, app)
    xx[PS_CMD_HOLD] = ps_cmd
    xx[PR_CMD_HOLD] = pr_cmd
    return xx, info


def physical_rhs(x: Array, q: Array, app: Apparatus, source_disturbance_W: float = 0.0, v_g: Array | None = None) -> Array:
    pp, s = app.plant, app.source
    vg = np.asarray(app.v_g_star if v_g is None else v_g, dtype=float)
    d = np.zeros_like(x)
    sigma, domega, _us = np.asarray(q, dtype=float)
    v_sw = x[VSW_HOLD]
    d[VO] = sigma*x[VO] + domega*(J@x[VO])
    d[IF] = (v_sw-x[VC]-pp.R_f_ohm*x[IF]-pp.omega_0*pp.L_f_H*(J@x[IF]))/pp.L_f_H
    d[VC] = (x[IF]-x[IG]-pp.omega_0*pp.C_f_F*(J@x[VC]))/pp.C_f_F
    d[IG] = (x[VC]-vg-pp.R_g_ohm*x[IG]-pp.omega_0*pp.L_g_H*(J@x[IG]))/pp.L_g_H
    d[E_DC] = x[P_S] - KAPPA_P*float(v_sw@x[IF]) - loss_converter(x[IF], pp)
    d[P_S] = (-x[P_S]+x[PS_CMD_HOLD]+source_disturbance_W)/s.tau_s
    ls = loss_source(x[P_S], x[E_S], s)
    d[E_S] = x[P_R]-x[P_S]-ls
    d[P_R] = (-x[P_R]+x[PR_CMD_HOLD])/s.tau_recharge_s
    # controller/filter/hold states are piecewise constant between events
    return d


def integrate_physical(x: Array, q: Array, dt: float, app: Apparatus, nsub: int = 5) -> Array:
    xx = x.copy()
    h = dt/max(1, nsub)
    for _ in range(max(1, nsub)):
        k1 = physical_rhs(xx, q, app)
        k2 = physical_rhs(xx+0.5*h*k1, q, app)
        k3 = physical_rhs(xx+0.5*h*k2, q, app)
        k4 = physical_rhs(xx+h*k3, q, app)
        xx += h*(k1+2*k2+2*k3+k4)/6.0
    return xx


def hold_transition(x: Array, q: Array, T: float, phase_fast: float, phase_source: float, mode: HybridMode, app: Apparatus) -> tuple[Array, float, float, HybridMode, dict[str, float]]:
    """Exact event order for fixed fast/source clocks and an asynchronous outer hold."""
    xx = x.copy()
    t = 0.0
    pf, psph = float(phase_fast), float(phase_source)
    max_i = float(np.linalg.norm(xx[IF])); min_vdc = Vdc_from_energy(xx[E_DC], app.plant); max_m = float(np.linalg.norm(xx[VSW_HOLD]))/modulation_radius(Vdc_from_energy(xx[E_DC], app.plant), app)
    max_guard = 0.0
    tol = 1e-14
    while t < T-tol:
        tf = app.timing.T_fast_s-pf if pf > tol else app.timing.T_fast_s
        ts = app.timing.T_source_s-psph if psph > tol else app.timing.T_source_s
        dt = min(tf, ts, T-t)
        xx = integrate_physical(xx, q, dt, app, nsub=max(1, int(math.ceil(dt/10e-6))))
        t += dt; pf += dt; psph += dt
        max_i = max(max_i, float(np.linalg.norm(xx[IF])))
        min_vdc = min(min_vdc, Vdc_from_energy(xx[E_DC], app.plant))
        max_m = max(max_m, float(np.linalg.norm(xx[VSW_HOLD]))/max(modulation_radius(Vdc_from_energy(xx[E_DC], app.plant), app),1e-9))
        if abs(pf-app.timing.T_fast_s) < 5e-13 or pf > app.timing.T_fast_s:
            xx, mode, gd = fast_event(xx, q, mode, app)
            max_guard = max(max_guard, gd["intervention_V"])
            pf = 0.0
        if abs(psph-app.timing.T_source_s) < 5e-13 or psph > app.timing.T_source_s:
            xx, _ = source_event(xx, q, app)
            psph = 0.0
    return xx, pf, psph, mode, {"max_i_f_A": max_i, "min_Vdc_V": min_vdc, "max_modulation_fraction": max_m, "max_guard_intervention_V": max_guard}


def augmented_transition(x: Array, q: Array, u: Array, T: float, phase_fast: float, phase_source: float, mode: HybridMode, app: Apparatus) -> tuple[Array, float, float, Array, HybridMode, dict[str,float]]:
    xn, pf, psph, mn, stats = hold_transition(x, q, T, phase_fast, phase_source, mode, app)
    return xn, pf, psph, np.asarray(u, dtype=float).copy(), mn, stats


def continuous_normal_rhs(xc: Array, q: Array, app: Apparatus) -> Array:
    """Smooth unsaturated continuous normal branch; 28 states, excludes hold states."""
    x = np.asarray(xc, dtype=float)
    q = np.asarray(q, dtype=float)
    d = np.zeros_like(x)
    y = x[18:28]
    yif,yvc,yig,yvg = y[0:2],y[2:4],y[4:6],y[6:8]
    ev = x[VO]-yvc
    iref = yig + app.plant.omega_0*app.plant.C_f_F*(J@yvc) + app.K_Pv_A_per_V*ev + app.K_Iv_A_per_Vs*x[XI_V]
    ei = iref-yif
    ichp=(yif-yig)-x[X_AD]
    vraw=yvc+app.plant.R_f_ohm*yif+app.plant.omega_0*app.plant.L_f_H*(J@yif)+app.K_Pi_ohm*ei+app.K_Ii_ohm_per_s*x[XI_I]-app.controller.k_active_damping_ohm*ichp
    d[VO]=q[0]*x[VO]+q[1]*(J@x[VO])
    d[IF]=(vraw-x[VC]-app.plant.R_f_ohm*x[IF]-app.plant.omega_0*app.plant.L_f_H*(J@x[IF]))/app.plant.L_f_H
    d[VC]=(x[IF]-x[IG]-app.plant.omega_0*app.plant.C_f_F*(J@x[VC]))/app.plant.C_f_F
    d[IG]=(x[VC]-yvg-app.plant.R_g_ohm*x[IG]-app.plant.omega_0*app.plant.L_g_H*(J@x[IG]))/app.plant.L_g_H
    d[E_DC]=x[P_S]-KAPPA_P*float(vraw@x[IF])-loss_converter(x[IF],app.plant)
    d[P_S]=q[2]
    ls=loss_source(x[P_S],x[E_S],app.source)
    d[E_S]=x[P_R]-x[P_S]-ls
    prcmd=x[P_S]+ls+app.source.k_buffer_energy_per_s*(app.source.E_buffer_star_J-x[E_S])
    d[P_R]=(-x[P_R]+prcmd)/app.source.tau_recharge_s
    d[XI_V]=ev
    d[XI_I]=ei
    d[X_AD]=app.omega_ad*((yif-yig)-x[X_AD])
    wf=2*math.pi*app.measurement.f_ac_filter_Hz; wd=2*math.pi*app.measurement.f_dc_filter_Hz
    d[18:20]=wf*(x[IF]-yif); d[20:22]=wf*(x[VC]-yvc); d[22:24]=wf*(x[IG]-yig)
    d[24:26]=wf*(np.asarray(app.v_g_star)-yvg)
    d[26]=wd*(Vdc_from_energy(x[E_DC],app.plant)-y[8]); d[27]=wd*(x[P_S]-y[9])
    return d


def continuous_matrices(app: Apparatus) -> tuple[Array,Array,Dict[str,Array]]:
    n=28
    def S(a:int,b:int)->Array: return np.eye(n)[a:b,:]
    Svo,Sif,Svc,Sig=S(0,2),S(2,4),S(4,6),S(6,8)
    SE,Sps,SEs,Spr=S(8,9),S(9,10),S(10,11),S(11,12)
    Sxv,Sxi,Sxad=S(12,14),S(14,16),S(16,18)
    Syif,Syvc,Syig,Syvg=S(18,20),S(20,22),S(22,24),S(24,26)
    SyV,Syps=S(26,27),S(27,28)
    Ciref=Syig+app.plant.omega_0*app.plant.C_f_F*J@Syvc+app.K_Pv_A_per_V*(Svo-Syvc)+app.K_Iv_A_per_Vs*Sxv
    Cei=Ciref-Syif
    Cichp=Syif-Syig-Sxad
    Cvraw=Syvc+app.plant.R_f_ohm*Syif+app.plant.omega_0*app.plant.L_f_H*J@Syif+app.K_Pi_ohm*Cei+app.K_Ii_ohm_per_s*Sxi-app.controller.k_active_damping_ohm*Cichp
    A=np.zeros((n,n)); B=np.zeros((n,3))
    B[0:2,0:2]=np.column_stack((np.asarray(app.x_star)[VO],J@np.asarray(app.x_star)[VO])); B[P_S,2]=1.0
    A[IF,:]=(Cvraw-Svc-app.plant.R_f_ohm*Sif-app.plant.omega_0*app.plant.L_f_H*J@Sif)/app.plant.L_f_H
    A[VC,:]=(Sif-Sig-app.plant.omega_0*app.plant.C_f_F*J@Svc)/app.plant.C_f_F
    A[IG,:]=(Svc-Syvg-app.plant.R_g_ohm*Sig-app.plant.omega_0*app.plant.L_g_H*J@Sig)/app.plant.L_g_H
    x0=np.asarray(app.x_star); vraw=np.asarray(app.v_sw_star); if0=x0[IF]
    A[E_DC,:]=Sps-KAPPA_P*(if0.reshape(1,2)@Cvraw+vraw.reshape(1,2)@Sif)-grad_loss_converter(if0,app.plant).reshape(1,2)@Sif
    dlp,dle=loss_source_grad(x0[P_S],x0[E_S],app.source)
    A[E_S,:]=Spr-(1.0+dlp)*Sps-dle*SEs
    A[P_R,:]=((1.0+dlp)*Sps+(dle-app.source.k_buffer_energy_per_s)*SEs-Spr)/app.source.tau_recharge_s
    A[XI_V,:]=Svo-Syvc; A[XI_I,:]=Cei; A[X_AD,:]=app.omega_ad*(Syif-Syig-Sxad)
    wf=2*math.pi*app.measurement.f_ac_filter_Hz; wd=2*math.pi*app.measurement.f_dc_filter_Hz
    A[18:20,:]=wf*(Sif-Syif); A[20:22,:]=wf*(Svc-Syvc); A[22:24,:]=wf*(Sig-Syig); A[24:26,:]=-wf*Syvg
    dVdE=1.0/(app.plant.C_dc_F*app.plant.V_dc_star_V)
    A[26,:]=wd*(dVdE*SE-SyV); A[27,:]=wd*(Sps-Syps)
    return A,B,{"C_iref":Ciref,"C_ei":Cei,"C_vraw":Cvraw}


def inner_true_measurement_matrix(app: Apparatus) -> Array:
    # state [if,vc,ig,xi_v,xi_i,xad], vo and vg fixed
    A=np.zeros((12,12)); pp=app.plant; Kpv,Kiv,Kpi,Kii,Kad=app.K_Pv_A_per_V,app.K_Iv_A_per_Vs,app.K_Pi_ohm,app.K_Ii_ohm_per_s,app.controller.k_active_damping_ohm
    # e_i = ig + wCfJ vc - Kpv vc + Kiv xiv - if (vo fixed)
    A[0:2,0:2]=-(Kpi+Kad)/pp.L_f_H*I2
    A[0:2,2:4]=Kpi/pp.L_f_H*(pp.omega_0*pp.C_f_F*J-Kpv*I2)
    A[0:2,4:6]=(Kpi+Kad)/pp.L_f_H*I2
    A[0:2,6:8]=Kpi*Kiv/pp.L_f_H*I2
    A[0:2,8:10]=Kii/pp.L_f_H*I2
    A[0:2,10:12]=Kad/pp.L_f_H*I2
    A[2:4,0:2]=1/pp.C_f_F*I2; A[2:4,2:4]=-pp.omega_0*J; A[2:4,4:6]=-1/pp.C_f_F*I2
    A[4:6,2:4]=1/pp.L_g_H*I2; A[4:6,4:6]=-(pp.R_g_ohm/pp.L_g_H)*I2-pp.omega_0*J
    A[6:8,2:4]=-I2
    A[8:10,0:2]=-I2; A[8:10,2:4]=pp.omega_0*pp.C_f_F*J-Kpv*I2; A[8:10,4:6]=I2; A[8:10,6:8]=Kiv*I2
    A[10:12,0:2]=app.omega_ad*I2; A[10:12,4:6]=-app.omega_ad*I2; A[10:12,10:12]=-app.omega_ad*I2
    return A


def open_physical_matrices(app: Apparatus) -> dict[str, Array]:
    """Local open-loop matrices for x_p=[v_o(2),i_f(2),v_c(2),i_g(2),E_dc,p_s,E_s,p_r].

    Inputs are xi(2), v_sw(2), v_g(2), p_s_cmd, p_r_cmd and d_s.
    The bilinear bridge-power and nonlinear buffer-loss remainders are
    linearized at the declared operating point; their exact nonlinear forms
    remain in physical_rhs.
    """
    pp, ss = app.plant, app.source
    n = 12
    A = np.zeros((n, n))
    B_xi = np.zeros((n, 2))
    B_vsw = np.zeros((n, 2))
    E_vg = np.zeros((n, 2))
    B_ps_cmd = np.zeros((n, 1))
    B_pr_cmd = np.zeros((n, 1))
    E_ds = np.zeros((n, 1))
    x0 = np.asarray(app.x_star)
    B_xi[0:2, :] = np.column_stack((x0[VO], J @ x0[VO]))
    A[2:4, 2:4] = -(pp.R_f_ohm/pp.L_f_H)*I2 - pp.omega_0*J
    A[2:4, 4:6] = -I2/pp.L_f_H
    B_vsw[2:4, :] = I2/pp.L_f_H
    A[4:6, 2:4] = I2/pp.C_f_F
    A[4:6, 4:6] = -pp.omega_0*J
    A[4:6, 6:8] = -I2/pp.C_f_F
    A[6:8, 4:6] = I2/pp.L_g_H
    A[6:8, 6:8] = -(pp.R_g_ohm/pp.L_g_H)*I2 - pp.omega_0*J
    E_vg[6:8, :] = -I2/pp.L_g_H
    A[8, 2:4] = -KAPPA_P*np.asarray(app.v_sw_star) - grad_loss_converter(x0[IF], pp)
    A[8, 9] = 1.0
    B_vsw[8, :] = -KAPPA_P*x0[IF]
    A[9, 9] = -1.0/ss.tau_s
    B_ps_cmd[9, 0] = 1.0/ss.tau_s
    E_ds[9, 0] = 1.0/ss.tau_s
    dlp, dle = loss_source_grad(x0[P_S], x0[E_S], ss)
    A[10, 9] = -(1.0 + dlp)
    A[10, 10] = -dle
    A[10, 11] = 1.0
    A[11, 11] = -1.0/ss.tau_recharge_s
    B_pr_cmd[11, 0] = 1.0/ss.tau_recharge_s
    return {
        "A_open_physical_12": A,
        "B_open_xi_12x2": B_xi,
        "B_open_vsw_12x2": B_vsw,
        "E_open_vg_12x2": E_vg,
        "B_open_ps_cmd_12x1": B_ps_cmd,
        "B_open_pr_cmd_12x1": B_pr_cmd,
        "E_open_ds_12x1": E_ds,
    }


def measurement_matrices(app: Apparatus) -> tuple[Array,Array,Array,Array]:
    # Physical subset [vo2,if2,vc2,ig2,E,ps,Es,pr] = 12
    C=np.zeros((10,12)); D=np.zeros((10,2))
    C[0:2,2:4]=I2; C[2:4,4:6]=I2; C[4:6,6:8]=I2; D[6:8,:]=I2
    C[8,8]=1.0/(app.plant.C_dc_F*app.plant.V_dc_star_V); C[9,9]=1.0
    w=np.r_[np.full(8,2*math.pi*app.measurement.f_ac_filter_Hz),np.full(2,2*math.pi*app.measurement.f_dc_filter_Hz)]
    Af=-np.diag(w); Bf=np.diag(w)
    return C,D,Af,Bf


def recovered_static_map(old_Nv:Array,old_Ke:Array)->Array:
    return np.hstack((old_Nv,-old_Ke))


def branchB_static_map(app:Apparatus)->Array:
    # instantaneous normal command wrt [vo,if,vc,ig], integrators and xad fixed
    M=np.zeros((2,8)); pp=app.plant
    M[:,0:2]=app.K_Pi_ohm*app.K_Pv_A_per_V*I2
    M[:,2:4]=pp.R_f_ohm*I2+pp.omega_0*pp.L_f_H*J-app.K_Pi_ohm*I2-app.controller.k_active_damping_ohm*I2
    M[:,4:6]=I2+app.K_Pi_ohm*(pp.omega_0*pp.C_f_F*J-app.K_Pv_A_per_V*I2)
    M[:,6:8]=app.K_Pi_ohm*I2+app.controller.k_active_damping_ohm*I2
    return M


def simulate_nominal(app:Apparatus,duration:float=0.02,perturb:bool=False)->dict[str,Any]:
    x=np.asarray(app.x_star,dtype=float).copy(); q=np.asarray(app.q_star,dtype=float); uq=q.copy(); mode=HybridMode(); pf=0.0; psph=0.0
    if perturb:
        x[VC]+=np.array([1.0,-0.5]); x[E_DC]+=5.0; x[P_S]+=500.0
    t=0.0; maxdev=0.0; maxi=0.0; minv=1e9; maxm=0.0
    while t < duration-1e-15:
        q=uq.copy()
        # q_k acts during this hold; calculate the next command from the t_k snapshot.
        uq=nominal_action(x,app)
        T=min(app.timing.T_outer_nom_s,duration-t)
        x,pf,psph,_,mode,stats=augmented_transition(x,q,uq,T,pf,psph,mode,app)
        t+=T
        maxdev=max(maxdev,float(np.max(np.abs(x-np.asarray(app.x_star)))))
        maxi=max(maxi,stats["max_i_f_A"]); minv=min(minv,stats["min_Vdc_V"]); maxm=max(maxm,stats["max_modulation_fraction"])
    return {"duration_s":duration,"perturbed":perturb,"final_state":x.tolist(),"final_q_active":q.tolist(),"final_q_queued":uq.tolist(),"max_abs_state_deviation":maxdev,"max_i_f_A":maxi,"min_Vdc_V":minv,"max_modulation_fraction":maxm,"hardware_trip":mode.hardware_trip}


def validate(app:Apparatus,old_Nv:Array,old_Ke:Array,old_A:Array)->tuple[dict[str,Any],dict[str,Array]]:
    x0=np.asarray(app.x_star,dtype=float); q0=np.zeros(3); pp=app.plant; s=app.source
    # exact equilibrium residuals
    rhs=physical_rhs(x0,q0,app)
    r_if=np.asarray(app.v_sw_star)-x0[VC]-pp.R_f_ohm*x0[IF]-pp.omega_0*pp.L_f_H*(J@x0[IF])
    r_vc=x0[IF]-x0[IG]-pp.omega_0*pp.C_f_F*(J@x0[VC])
    r_ig=x0[VC]-np.asarray(app.v_g_star)-pp.R_g_ohm*x0[IG]-pp.omega_0*pp.L_g_H*(J@x0[IG])
    dc_balance=x0[P_S]-app.P_bridge_star_W-loss_converter(x0[IF],pp)
    src_balance=x0[P_R]-x0[P_S]-loss_source(x0[P_S],x0[E_S],s)
    # dVOC
    f=dvoc_relative_field(x0[VO],x0[YF][4:6],app); xi=intrinsic_action(x0[VO],f,app)
    # control event equilibrium
    xe,mode,gd=fast_event(x0,q0,HybridMode(),app); xs,sinfo=source_event(xe,q0,app)
    event_res=float(np.max(np.abs(xs-x0)))
    # continuous matrices
    A,B,maps=continuous_matrices(app); xc=x0[:28]
    Afd=finite_difference_jacobian(lambda z: continuous_normal_rhs(z,q0,app),xc)
    Bfd=np.zeros_like(B)
    for j,scale in enumerate([1.0,1.0,1e5]):
        h=2e-6*scale; qp=q0.copy(); qm=q0.copy(); qp[j]+=h; qm[j]-=h
        Bfd[:,j]=(continuous_normal_rhs(xc,qp,app)-continuous_normal_rhs(xc,qm,app))/(2*h)
    # nominal action linearization and nominal closed-loop poles
    Kq=finite_difference_jacobian(lambda z: nominal_action(np.r_[z,np.asarray(app.x_star)[28:32]],app),xc)
    Acl=A+B@Kq
    poles=eigvals(Acl)
    Ainner=inner_true_measurement_matrix(app); inner_poles=eigvals(Ainner)
    # LCL
    Al=np.zeros((6,6)); Bl=np.zeros((6,2)); El=np.zeros((6,2))
    Al[0:2,0:2]=-(pp.R_f_ohm/pp.L_f_H)*I2-pp.omega_0*J; Al[0:2,2:4]=-1/pp.L_f_H*I2; Bl[0:2,:]=1/pp.L_f_H*I2
    Al[2:4,0:2]=1/pp.C_f_F*I2; Al[2:4,2:4]=-pp.omega_0*J; Al[2:4,4:6]=-1/pp.C_f_F*I2
    Al[4:6,2:4]=1/pp.L_g_H*I2; Al[4:6,4:6]=-(pp.R_g_ohm/pp.L_g_H)*I2-pp.omega_0*J; El[4:6,:]=-1/pp.L_g_H*I2
    lcl_poles=eigvals(Al)
    # matrices/filter
    Cy,Dy,Af,Bf=measurement_matrices(app)
    Td=app.timing.T_fast_s; ad=np.exp(np.diag(Af)*Td); Afdig=np.diag(ad); Bfdig=np.diag(1-ad)
    # queue causality
    u1=np.array([0.1,0.2,1000.0]); u2=-u1
    g1=augmented_transition(x0,q0,u1,app.timing.T_outer_nom_s,0.0,0.0,HybridMode(),app)
    g2=augmented_transition(x0,q0,u2,app.timing.T_outer_nom_s,0.0,0.0,HybridMode(),app)
    queue_ind=float(np.max(np.abs(g1[0]-g2[0]))); assign=max(float(np.max(np.abs(g1[3]-u1))),float(np.max(np.abs(g2[3]-u2))))
    # one-hold zero consistency and simulations
    xh,pf,psph,_,mh,stats=augmented_transition(x0,q0,q0,app.timing.T_outer_nom_s,0.0,0.0,HybridMode(),app)
    hold_res=float(np.max(np.abs(xh-x0)))
    sim0=simulate_nominal(app,0.02,False); simp=simulate_nominal(app,0.05,True)
    # recovered/new maps and exact reconstruction of the historical 10-state Jacobian
    Mold=recovered_static_map(old_Nv,old_Ke); Mnew=branchB_static_map(app)
    old_map_norm=float(np.linalg.norm(Mold))
    Aold_rec=np.zeros((10,10))
    open_if=np.hstack((np.zeros((2,2)), pp.R_f_ohm*I2+pp.omega_0*pp.L_f_H*J, I2, np.zeros((2,2))))
    Aold_rec[2:4,0:8]=(Mold-open_if)/pp.L_f_H
    Aold_rec[4:6,2:4]=I2/pp.C_f_F; Aold_rec[4:6,4:6]=-pp.omega_0*J; Aold_rec[4:6,6:8]=-I2/pp.C_f_F
    Aold_rec[6:8,4:6]=I2/pp.L_g_H; Aold_rec[6:8,6:8]=-(pp.R_g_ohm/pp.L_g_H)*I2-pp.omega_0*J
    Aold_rec[8,0:8]=-KAPPA_P*(x0[IF].reshape(1,2)@Mold + np.hstack((np.zeros((1,2)),np.asarray(app.v_sw_star).reshape(1,2),np.zeros((1,4)))))
    Aold_rec[8,9]=1.0
    recovered_A_res=float(np.linalg.norm(Aold_rec-old_A,ord=np.inf))
    # feasibility
    Vdc=Vdc_from_energy(x0[E_DC],pp); mnorm=SQRT3*np.linalg.norm(np.asarray(app.v_sw_star))/Vdc
    Vs=Vsource_from_energy(x0[E_S],s); Is=x0[P_S]/Vs
    domain_abs={
        "v_o_magnitude_V":[NORMAL_DOMAIN_PU["v_o_magnitude"][0]*app.V_star_V,NORMAL_DOMAIN_PU["v_o_magnitude"][1]*app.V_star_V],
        "V_dc_V":[NORMAL_DOMAIN_PU["V_dc"][0]*pp.V_dc_star_V,NORMAL_DOMAIN_PU["V_dc"][1]*pp.V_dc_star_V],
        "i_f_magnitude_A":[0.0,NORMAL_DOMAIN_PU["i_f_magnitude"][1]*pp.I_phase_peak_A],
        "i_g_magnitude_A":[0.0,NORMAL_DOMAIN_PU["i_g_magnitude"][1]*pp.I_phase_peak_A],
        "modulation_norm":[0.0,app.modulator.m_max],
        "p_s_W":[s.p_s_min_W,s.p_s_max_W],
        "source_buffer_voltage_V":[s.V_buffer_min_V,s.V_buffer_max_V],
    }
    equilibrium_in_domain=(
        domain_abs["v_o_magnitude_V"][0] <= np.linalg.norm(x0[VO]) <= domain_abs["v_o_magnitude_V"][1]
        and domain_abs["V_dc_V"][0] <= Vdc <= domain_abs["V_dc_V"][1]
        and np.linalg.norm(x0[IF]) <= domain_abs["i_f_magnitude_A"][1]
        and np.linalg.norm(x0[IG]) <= domain_abs["i_g_magnitude_A"][1]
        and mnorm <= app.modulator.m_max
        and s.p_s_min_W <= x0[P_S] <= s.p_s_max_W
        and s.V_buffer_min_V <= Vs <= s.V_buffer_max_V
    )
    # sampled nominal map at aligned 500 us, fixed clock phase and inactive guard
    z0=np.r_[x0,q0]
    def Gnom(z:Array)->Array:
        xx=z[:N_X]; qq=z[N_X:]; uu=nominal_action(xx,app)
        xn,_,_,un,_,_=augmented_transition(xx,qq,uu,app.timing.T_outer_nom_s,0.0,0.0,HybridMode(),app)
        return np.r_[xn,un]
    F500=finite_difference_jacobian(Gnom,z0,rel_step=5e-7)
    eigF=eigvals(F500)

    relA=float(np.linalg.norm(A-Afd,ord=np.inf)/max(1.0,np.linalg.norm(A,ord=np.inf)))
    relB=float(np.linalg.norm(B-Bfd,ord=np.inf)/max(1.0,np.linalg.norm(B,ord=np.inf)))
    checks={
        "state_inventory_dimensions": N_X==len(STATE_UNITS)==32,
        "physical_equilibrium_residual": float(np.max(np.abs(rhs)))<1e-8,
        "lcl_equilibrium_residual": max(np.linalg.norm(r_if,np.inf),np.linalg.norm(r_vc,np.inf),np.linalg.norm(r_ig,np.inf))<1e-9,
        "dc_power_balance": abs(dc_balance)<1e-8,
        "source_energy_balance": abs(src_balance)<1e-8,
        "dvoc_equilibrium_compatibility": float(np.linalg.norm(xi))<1e-10,
        "modulation_feasibility": mnorm<app.modulator.m_max,
        "source_power_current_energy_feasibility": s.p_s_min_W<x0[P_S]<s.p_s_max_W and abs(Is)<s.I_buffer_max_A and s.V_buffer_min_V<Vs<s.V_buffer_max_V,
        "equilibrium_in_declared_normal_domain": bool(equilibrium_in_domain),
        "measurement_routing_complete": Cy.shape==(10,12) and Dy.shape==(10,2),
        "recovered_static_map_reproduces_historical_A": recovered_A_res<1e-9,
        "controller_normal_branch_stable": float(np.max(np.real(inner_poles)))<0.0,
        "nominal_continuous_closed_loop_stable": float(np.max(np.real(poles)))<1e-6,
        "analytic_numeric_jacobian": relA<1e-8 and relB<1e-8,
        "queue_causality": queue_ind<1e-11 and assign<1e-12,
        "zero_disturbance_one_hold": hold_res<1e-7,
        "zero_disturbance_multirate_simulation": sim0["max_abs_state_deviation"]<1e-5 and not sim0["hardware_trip"],
    }
    validation={
        "decision":"B. CANONICAL REFERENCE APPARATUS DESIGNED AND CLOSED",
        "branch_A":"NOT IDENTIFIABLE",
        "all_model_closure_checks_pass":bool(all(checks.values())),
        "checks":checks,
        "dimensions":{"continuous_hybrid_state":32,"timing_phase_states":2,"guard_release_timer_state":1,"queued_action":3,"outer_augmented_numeric_dimension":38,"measurement_filter_states":10,"controller_dynamic_states":6,"source_dynamic_states":3,"guard_discrete_mode_flags":2},
        "equilibrium":{"x_star":x0.tolist(),"q_star":q0.tolist(),"v_g_star_V":list(app.v_g_star),"v_sw_star_V":list(app.v_sw_star),"P_pcc_W":app.P_pcc_star_W,"Q_pcc_var":app.Q_pcc_star_var,"P_bridge_W":app.P_bridge_star_W,"converter_loss_W":app.p_converter_loss_star_W,"p_s_W":app.p_s_star_W,"source_loss_W":app.p_source_loss_star_W,"p_recharge_W":app.p_r_star_W},
        "residuals":{"physical_rhs_inf":float(np.max(np.abs(rhs))),"inverter_inductor_V_inf":float(np.linalg.norm(r_if,np.inf)),"capacitor_current_A_inf":float(np.linalg.norm(r_vc,np.inf)),"grid_inductor_V_inf":float(np.linalg.norm(r_ig,np.inf)),"dc_power_W":float(dc_balance),"source_energy_W":float(src_balance),"dvoc_field_V_s_inf":float(np.max(np.abs(f))),"nominal_intrinsic_action_inf":float(np.max(np.abs(xi))),"digital_event_inf":event_res,"one_hold_state_inf":hold_res,"jacobian_A_relative_inf":relA,"jacobian_B_relative_inf":relB,"queue_physical_dependence_on_u_inf":queue_ind,"queue_assignment_inf":assign,"recovered_static_map_A_inf":recovered_A_res},
        "dvoc":{"eta_ohm_per_s":app.eta_ohm_per_s,"alpha_siemens":app.alpha_S,"kappa_rad":app.kappa_rad,"kappa_deg":math.degrees(app.kappa_rad),"V_star_V":app.V_star_V,"P_star_W":app.P_star_W,"Q_star_var":app.Q_star_var,"local_radial_rate_2_eta_alpha_per_s":2*app.eta_ohm_per_s*app.alpha_S,"local_radial_time_constant_s":1/(2*app.eta_ohm_per_s*app.alpha_S)},
        "controller":{"K_Pv_A_per_V":app.K_Pv_A_per_V,"K_Iv_A_per_Vs":app.K_Iv_A_per_Vs,"K_Pi_ohm":app.K_Pi_ohm,"K_Ii_ohm_per_s":app.K_Ii_ohm_per_s,"active_damping_ohm":app.controller.k_active_damping_ohm,"inner_poles":eig_records(inner_poles),"nominal_continuous_poles":eig_records(poles),"sampled_500us_eigenvalues":eig_records(eigF)},
        "lcl":{"undamped_resonance_Hz":pp.f_lcl_Hz,"open_loop_poles":eig_records(lcl_poles)},
        "modulation":{"implementation":"SVPWM; conservative circular subset of linear hexagon","m_norm_equilibrium":mnorm,"m_limit":app.modulator.m_max,"bridge_voltage_norm_V":float(np.linalg.norm(app.v_sw_star)),"bridge_voltage_limit_V":modulation_radius(Vdc,app),"headroom_V":modulation_radius(Vdc,app)-float(np.linalg.norm(app.v_sw_star)),"dead_time_s":app.modulator.dead_time_s},
        "source":{"type":"utility-backed programmable bidirectional DC source with finite capacitor buffer","V_buffer_V":Vs,"I_buffer_A":Is,"p_s_limits_W":[s.p_s_min_W,s.p_s_max_W],"u_s_limit_W_s":s.u_s_max_W_s,"tracking_disturbance_requirement_W":s.d_s_bound_W,"tracking_rate_error_requirement_W_s":s.d_s_bound_W/s.tau_s},
        "normal_action_bounds":ACTION_BOUNDS,
        "declared_normal_domain":{"status":"design template for Gate B1/B2; not yet a certified invariant set","per_unit":NORMAL_DOMAIN_PU,"absolute":domain_abs},
        "guard":{"owner":"separate fast inverter-side-current barrier projection","I_warning_A":app.guard.I_warning_pu*pp.I_phase_peak_A,"I_limit_A":app.guard.I_limit_pu*pp.I_phase_peak_A,"I_release_A":app.guard.I_release_pu*pp.I_phase_peak_A,"I_trip_A":app.guard.I_trip_pu*pp.I_phase_peak_A,"max_intervention_V":app.guard.max_intervention_V,"voltage_slew_V_s":app.guard.voltage_slew_V_per_s,"normal_equilibrium_active":bool(gd["active"]),"composition_theorem_claimed":False},
        "static_map_comparison":{"recovered_map_norm":old_map_norm,"relative_frobenius_difference":float(np.linalg.norm(Mnew-Mold)/np.linalg.norm(Mold)),"statement":"Branch B is new and is not forced to reproduce an undocumented historical dynamic controller."},
        "timing":{"outer_hold_unknown_s":[app.timing.T_outer_min_s,app.timing.T_outer_max_s],"fast_clock_s":app.timing.T_fast_s,"source_clock_s":app.timing.T_source_s,"clock_phase_states_required":True,"queue":"q_k acts on [t_k,t_{k+1}); u_k calculated at t_k; q_{k+1}=u_k"},
        "simulations":{"zero_disturbance":sim0,"small_perturbation":simp},
        "scope":{"invariant_certificate_attempted":False,"one_hold_certificate_attempted":False,"recursive_feasibility_claimed":False,"next_gate":"B1 conference one-hold certificate"},
    }
    open_phys = open_physical_matrices(app)
    arrays={
        "J":J,"T_clarke_amplitude_invariant":clarke_amplitude_invariant(),"x_star_32":x0,"q_star_3":q0,
        **open_phys,
        "v_g_star":np.asarray(app.v_g_star),"v_sw_star":np.asarray(app.v_sw_star),
        "A_lcl_open_6":Al,"B_lcl_vsw_6x2":Bl,"E_lcl_vg_6x2":El,
        "C_y_physical_10x12":Cy,"D_y_vg_10x2":Dy,"A_filter_continuous_10":Af,"B_filter_continuous_10":Bf,"A_filter_discrete_50us_10":Afdig,"B_filter_discrete_50us_10":Bfdig,
        "A_continuous_normal_analytic_28":A,"A_continuous_normal_fd_28":Afd,"B_q_continuous_28x3":B,"B_q_continuous_fd_28x3":Bfd,
        "K_q_nominal_3x28":Kq,"A_nominal_continuous_28":Acl,"A_inner_true_measurement_12":Ainner,"F_outer_nominal_500us_35":F500,
        "C_iref_2x28":maps["C_iref"],"C_ei_2x28":maps["C_ei"],"C_vraw_2x28":maps["C_vraw"],
        "static_map_recovered_2x8":Mold,"static_map_branchB_2x8":Mnew,"N_v_recovered":old_Nv,"K_e_recovered":old_Ke,"A_recovered_10":old_A,"A_reconstructed_from_recovered_static_map_10":Aold_rec,
        "normal_action_absolute_bounds_3":np.array([ACTION_BOUNDS["absolute"]["sigma_1_s"],ACTION_BOUNDS["absolute"]["Delta_omega_rad_s"],ACTION_BOUNDS["absolute"]["u_s_W_s"]]),
        "normal_action_slew_bounds_3":np.array([ACTION_BOUNDS["one_update_slew"]["sigma_1_s"],ACTION_BOUNDS["one_update_slew"]["Delta_omega_rad_s"],ACTION_BOUNDS["one_update_slew"]["u_s_W_s"]]),
    }
    return validation,arrays


def measurement_rows(app:Apparatus)->list[dict[str,str]]:
    m=app.measurement; rows=[]; delay=m.acquisition_delay_s+m.adc_transform_delay_s
    qi=2*m.current_full_scale_A/(2**m.adc_bits); qv=2*m.ac_voltage_full_scale_V/(2**m.adc_bits); qdc=m.dc_voltage_full_scale_V/(2**m.adc_bits); qp=2*m.source_power_full_scale_W/(2**m.adc_bits)
    def add(sig,sensor,frame,bw,Ts,noise,quant,dest): rows.append({"signal":sig,"sensor_location":sensor,"frame":"nominal-frequency dq" if frame=="dq" else frame,"filter_order":"1","bandwidth_Hz":f"{bw:g}","sample_period_s":f"{Ts:.9g}","front_end_delay_s":f"{delay:.9g}","bounded_noise":noise,"quantization_step":quant,"destination":dest,"classification":"N"})
    for a in ("d","q"):
        add(f"i_f_{a}","inverter-side LCL current", "dq",m.f_ac_filter_Hz,m.T_fast_sample_s,f"+/-{m.current_noise_bound_A} A",f"{qi:.9g} A","current PI; active damping; fast guard; predictor")
        add(f"v_c_{a}","LCL capacitor/PCC voltage", "dq",m.f_ac_filter_Hz,m.T_fast_sample_s,f"+/-{m.ac_voltage_noise_bound_V} V",f"{qv:.9g} V","voltage PI; current feedforward; predictor; PCC power")
        add(f"i_g_{a}","grid-side current", "dq",m.f_ac_filter_Hz,m.T_fast_sample_s,f"+/-{m.current_noise_bound_A} A",f"{qi:.9g} A","dVOC; voltage feedforward; predictor; PCC power")
        add(f"v_g_{a}","grid terminal voltage", "dq",m.f_ac_filter_Hz,m.T_fast_sample_s,f"+/-{m.ac_voltage_noise_bound_V} V",f"{qv:.9g} V","predictor/disturbance monitor; not a PLL input to dVOC")
    add("V_dc","DC-link voltage","scalar",m.f_dc_filter_Hz,m.T_fast_sample_s,f"+/-{m.dc_voltage_noise_bound_V} V",f"{qdc:.9g} V","SVPWM scaling; source/energy supervisor; predictor")
    add("p_s","source output-power estimate","scalar",m.f_dc_filter_Hz,m.T_fast_sample_s,f"+/-{m.source_power_noise_bound_W} W",f"{qp:.9g} W","source-rate law; predictor; source monitor")
    return rows


def write_controller_equations(path:Path,app:Apparatus,val:dict[str,Any])->None:
    lines=[]
    lines.append("# Canonical queued-action dVOC reference apparatus: controller equations\n")
    lines.append("## Evidence labels\n\nR = recovered artifact; P = primary-source equation; N = new design; V = inferred and verified; U = unresolved evidence.\n")
    lines.append("## 1. Frozen convention\n\n")
    lines.append(r"\[J=\begin{bmatrix}0&-1\\1&0\end{bmatrix},\quad P=\tfrac32 v_c^\top i_g,\quad Q=\tfrac32 v_c^\top J i_g.\]"+"\n\n")
    lines.append("The model uses amplitude-invariant Clarke variables. `v_o` is the internal dVOC reference, `v_c` the physical PCC/capacitor voltage, and `v_sw` the averaged bridge voltage. Treating published terminal-voltage dVOC as an internal reference feeding nested loops is a new cascaded implementation decision.\n\n")
    lines.append("## 2. Exact foundational dVOC law [P]\n\n")
    lines.append(r"\[\dot v_o=\omega_0Jv_o+\eta\left(Kv_o-R(\kappa)i_o+\alpha\phi(v_o)v_o\right),\]"+"\n")
    lines.append(r"\[K=\frac{R(\kappa)}{(V^\star)^2}\begin{bmatrix}P^\star&Q^\star\\-Q^\star&P^\star\end{bmatrix},\quad \phi(v_o)=\frac{(V^\star)^2-\|v_o\|^2}{(V^\star)^2}.\]"+"\n\n")
    lines.append("Citation: Seo, Colombino, Subotic, Johnson, Gross, and Dorfler, IEEE APEC 2019, DOI 10.1109/APEC.2019.8722028. The reference apparatus sets `i_o^dVOC = 1.5 hat(i_g)` so the published power normalization matches physical three-phase power.\n\n")
    lines.append("Selected values [N/V]:\n\n")
    lines.append(f"- V* = {app.V_star_V:.9f} V; P* = {app.P_star_W:.9f} W; Q* = {app.Q_star_var:.9f} var.\n")
    lines.append(f"- kappa = {app.kappa_rad:.9f} rad ({math.degrees(app.kappa_rad):.6f} deg).\n")
    lines.append(f"- eta = {app.eta_ohm_per_s:.9f} ohm/s from 0.5-Hz full-power droop.\n")
    lines.append(f"- alpha = {app.alpha_S:.9f} S from 5% voltage droop at 50 kvar.\n")
    lines.append(r"In the nominal-frequency dq frame, \(\xi_0=\operatorname{col}(\sigma_0,\Delta\omega_0)=[v_o\;Jv_o]^\top f_{\rm dVOC,dq}/\|v_o\|^2\), and physical frequency is \(\omega_0+\Delta\omega\)."+"\n\n")
    lines.append("### Gain sensitivity\n\n| Full-power P-f target | 3% V-Q | 5% V-Q | 10% V-Q |\n|---:|---:|---:|---:|\n")
    for df in (0.25,0.5,1.0):
        eta=2*math.pi*df*app.V_star_V**2/(app.plant.S_b_VA*math.sin(app.kappa_rad))
        Astar=math.cos(app.kappa_rad)*app.P_star_W+math.sin(app.kappa_rad)*app.Q_star_var
        vals=[]
        for dv in (.03,.05,.10):
            vals.append((Astar+math.sin(app.kappa_rad)*app.dvoc.reactive_design_var/(2*dv))/app.V_star_V**2)
        lines.append(f"| {df:.2f} Hz, eta={eta:.6f} ohm/s | {vals[0]:.6f} S | {vals[1]:.6f} S | {vals[2]:.6f} S |\n")
    lines.append("\n## 3. Averaged VSC/LCL/DC plant [R/V]\n\n")
    lines.append(r"\[L_f\dot i_f=v_{sw}-v_c-R_fi_f-\omega_0L_fJi_f,\]"+"\n")
    lines.append(r"\[C_f\dot v_c=i_f-i_g-\omega_0C_fJv_c,\quad L_g\dot i_g=v_c-v_g-R_gi_g-\omega_0L_gJi_g,\]"+"\n")
    lines.append(r"\[E_{dc}=\tfrac12C_{dc}V_{dc}^2,\quad \dot E_{dc}=p_s-\tfrac32v_{sw}^\top i_f-p_{loss}.\]"+"\n\n")
    lines.append("## 4. Cascaded inner controller [N]\n\n")
    lines.append(r"\[e_v=v_o-\hat v_c,\quad i_f^{\star,0}=\hat i_g+\omega_0C_fJ\hat v_c+K_{Pv}e_v+K_{Iv}\xi_v,\quad i_f^\star=\Pi_{\|i\|\le I_{ref,max}}(i_f^{\star,0}),\]"+"\n")
    lines.append(r"\[\dot\xi_v=e_v+(i_f^\star-i_f^{\star,0})/(K_{Iv}T_{aw,v}),\]"+"\n")
    lines.append(r"\[\dot x_{ad}=\omega_{ad}[(\hat i_f-\hat i_g)-x_{ad}],\quad i_{c,hp}=(\hat i_f-\hat i_g)-x_{ad},\]"+"\n")
    lines.append(r"\[v_{ref}^0=\hat v_c+R_f\hat i_f+\omega_0L_fJ\hat i_f+K_{Pi}(i_f^\star-\hat i_f)+K_{Ii}\xi_i-k_{ad}i_{c,hp},\]"+"\n")
    lines.append(r"\[\dot\xi_i=(i_f^\star-\hat i_f)+(v_{applied}-v_{ref}^0)/(K_{Ii}T_{aw,i}).\]"+"\n\n")
    lines.append(f"Gains: K_Pv={app.K_Pv_A_per_V:.9f} A/V, K_Iv={app.K_Iv_A_per_Vs:.9f} A/(V s), K_Pi={app.K_Pi_ohm:.9f} ohm, K_Ii={app.K_Ii_ohm_per_s:.9f} ohm/s, k_ad={app.controller.k_active_damping_ohm:.6f} ohm.\n\n")
    lines.append(r"""## 5. Measurement filters and routing [N]

For the ten scalar channels y=[i_f(2),v_c(2),i_g(2),v_g(2),V_dc,p_s],
\[\dot{\hat y}=\Omega_f(y-\hat y),\]
with 5-kHz poles on the eight AC channels and 1-kHz poles on V_dc and p_s. At a 50-us event the exact update is
\[\hat y^+=e^{-\Omega_fT_f}\hat y+(I-e^{-\Omega_fT_f})y_{sample}.\]
The controller uses the previously stored filtered sample, then updates the filters, producing one fast-sample measurement-to-actuation latency. `v_o` is internally generated and is not filtered.

## 6. SVPWM and physical modulation [P/N]

""")
    lines.append(r"\[m=\Pi_{\|m\|\le0.95}\left(\sqrt3\,v_{guard}/\hat V_{dc}\right),\quad v_{sw}=V_{dc}m/\sqrt3.\]"+"\n")
    lines.append("The circular set is a conservative subset of the SVPWM linear hexagon. It replaces the former energy-voltage surrogate.\n\n")
    lines.append("## 7. Programmable bidirectional source [N]\n\n")
    lines.append(r"\[\tau_s\dot p_s=-p_s+p_s^{cmd}+d_s,\quad p_s^{cmd}=\operatorname{sat}(p_s+\tau_su_s),\]"+"\n")
    lines.append(r"\[\dot E_s=p_r-p_s-p_{loss,s},\quad \tau_r\dot p_r=-p_r+p_r^{cmd},\]"+"\n")
    lines.append(r"\[p_r^{cmd}=\operatorname{sat}(p_s+p_{loss,s}+k_s(E_s^\star-E_s)).\]"+"\n")
    lines.append("The finite buffer obeys E_s=0.5*C_b*V_b^2 and the declared source loss is p_loss,s=P_0+R_s*p_s^2/V_b^2. This is a utility-backed programmable source emulator, not a renewable-specific model. In the unsaturated source-output branch, dot(p_s)=u_s+d_s/tau_s. The design contract is |d_s|<=250 W, hence |d_s/tau_s|<=50 kW/s; this is unresolved hardware evidence for Gate C, not a recovered fact.\n\n")
    lines.append("## 8. Current-protection owner [N]\n\n")
    lines.append(r"The 20-kHz guard owns inverter-side current. For \(h_g=I_{lim}^2-\|i_f\|^2\), it projects the voltage request onto \[2i_f^\top v\le2i_f^\top(v_c+R_fi_f+\omega_0L_fJi_f)+L_f\lambda_gh_g.\] It then enforces the modulation circle, a 100-V intervention cap, and a 5e6-V/s slew cap. An infeasible correction or 1.35-pu current causes hardware trip; no guard-composition theorem is claimed here."+"\n\n")
    lines.append("## 9. Hybrid sampled model\n\n")
    lines.append(r"""\[\chi_k=\operatorname{col}(x_k,\varphi_{f,k},\varphi_{s,k},r_{g,k},q_k;m_{g,k}),\]
\[G_T(\chi_k,u_k)=\operatorname{col}(F_T(x_k,q_k,\varphi_f,\varphi_s,m_g),\varphi_f^+,\varphi_s^+,r_g^+,u_k;m_g^+).\]
""")
    lines.append("The phase states are required because T in [495,505] microseconds is unknown while the fast and source clocks remain fixed at 50 and 100 microseconds. The guard release timer r_g is a numeric hybrid state and m_g contains the two discrete flags (active and hardware trip). The command u_k cannot change the current physical hold and becomes q_(k+1). The complete sampled state therefore has 38 numeric coordinates plus two discrete guard flags.\n\n")
    lines.append("The continuous/hybrid implementation state x has 32 real coordinates: physical converter/source states (12), PI and active-damping states (6), measurement-filter states (10), and held bridge/source commands (4). Adding two clock phases, the guard release timer, and q gives 38 numeric sampled coordinates; guard-active and hardware-trip are two discrete mode flags.\n\n")
    lines.append("## 10. Normal action authority and declared normal domain [N]\n\n")
    lines.append(r"\[|\sigma|\le20~\mathrm{s^{-1}},\quad |\Delta\omega|\le2\pi(2)~\mathrm{rad/s},\quad |u_s|\le1~\mathrm{MW/s},\]"+"\n")
    lines.append(r"\[|\Delta\sigma|\le8~\mathrm{s^{-1}},\quad |\Delta(\Delta\omega)|\le2\pi(0.8)~\mathrm{rad/s},\quad |\Delta u_s|\le0.5~\mathrm{MW/s}.\]"+"\n\n")
    lines.append("The smooth normal-domain template for the next certificate uses 0.85 <= ||v_o||/V* <= 1.15, 0.90 <= V_dc/V_dc* <= 1.10, ||i_f|| and ||i_g|| <= 1.10 I_b, ||m|| <= 0.95, the declared source limits, and a 700--900 V source-buffer window. This is a declared design domain, not yet a robust invariant or certified safe set.\n\n")
    lines.append("## 11. Exact routing diagram\n\n```text\nP*, Q*, V* --> foundational dVOC integrator --> v_o\n                                                |\n                                                v\nfiltered v_c,i_g --> voltage PI + capacitor feedforward --> i_f*\nfiltered i_f ----> current PI + capacitor-current active damping --> v_ref\n                                                               |\n                                                               v\n                                                fast i_f guard projection\n                                                               |\n                                                               v\n                                            SVPWM circular projection --> v_sw\n                                                               |\n                                                               v\nDC source --> DC link --> two-level averaged bridge --> L_f-C_f-L_g --> grid\n     ^                    |                          |\n     |                    +--> V_dc,i_f sensing     +--> v_c,i_g,v_g sensing\n     +-- u_s queue --> source-rate interface --> p_s\n```\n\n")
    lines.append("## 12. Pole summary\n\n| subsystem | max real part |\n|---|---:|\n")
    inner=max(p["real"] for p in val["controller"]["inner_poles"]); full=max(p["real"] for p in val["controller"]["nominal_continuous_poles"])
    lines.append(f"| true-measurement inner loop | {inner:.9f} 1/s |\n| complete nominal continuous normal branch | {full:.9f} 1/s |\n")
    lines.append("\n## 13. Claim boundary\n\nThis apparatus closes the model. It does not yet prove a hold-wide enclosure, invariant set, recursive feasibility, negative-reserve recovery, guard composition, switching residual bound, or target-processor timing.\n")
    path.write_text("".join(lines),encoding="utf-8")


def write_measurement_csv(path:Path,app:Apparatus)->None:
    rows=measurement_rows(app); fields=list(rows[0].keys())
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)


def write_timing_diagram(path:Path,app:Apparatus)->None:
    t=app.timing
    text=f"""# Digital timing and execution order

## Queue causality

At outer boundary `t_k`, the command queued previously becomes `q_k`. It acts throughout `[t_k,t_(k+1))`. The new command `u_k` is computed from the `t_k` filtered snapshot, but is only latched as `q_(k+1)` at the next outer boundary.

The future hold duration is unknown at action selection and belongs to `{1e6*t.T_outer_min_s:.0f}`–`{1e6*t.T_outer_max_s:.0f}` microseconds.

## Fixed clocks

| block | period | execution/update rule | declared delay |
|---|---:|---|---:|
| ADC acquisition | 50 us | simultaneous AC/DC capture | 2 us aperture |
| Clarke/dq transform and filters | 50 us | ten scalar first-order states | 8 us conversion/transform |
| current PI and active damping | 50 us | uses previous filtered sample | one fast sample total |
| fast current guard | 50 us | after current controller, before modulation | included in fast task |
| SVPWM | 50 us | zero-order hold to next PWM event | 2 us dead time |
| source-output and recharge commands | 100 us | held between source events | one source period |
| dVOC/action governor | nominal 500 us | asynchronous to fixed fast clocks | compute budget {1e6*t.outer_compute_budget_s:.0f} us |
| communication | outer task | included before queue latch | {1e6*t.communication_delay_s:.0f} us |

## Hybrid phase states

`phi_f in [0,50 us)` and `phi_s in [0,100 us)` are part of the sampled state. They determine the exact sequence of fast and source events during a jittered outer hold. A timing-dependent gain cannot be scheduled on the future T because T is not known when `u_k` is selected.

## Event order at a fast tick

1. compute current/voltage commands from the stored filtered sample;
2. apply anti-windup and active-damping state update;
3. execute the current guard and physical modulation projection;
4. latch the new bridge voltage for the next PWM interval;
5. update the ten measurement filters from the new acquisition.

At coincident source ticks, source command latching follows the fast/PWM event. At an outer boundary, queue latching follows any coincident fast/source event.
"""
    path.write_text(text,encoding="utf-8")


def jsonable(obj:Any)->Any:
    if isinstance(obj,np.ndarray): return obj.tolist()
    if isinstance(obj,(np.floating,np.integer,np.bool_)): return obj.item()
    if isinstance(obj,dict): return {k:jsonable(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)): return [jsonable(v) for v in obj]
    return obj


def write_manifest(path:Path,app:Apparatus,val:dict[str,Any],matrix_path:Path,generated:dict[str,Path],bundle:Path)->None:
    manifest={
        "decision":val["decision"],
        "branch_A":{"status":"NOT IDENTIFIABLE","searched_scope":"supplied manuscript, supplement, numerical bundles, audit scripts, and uploaded text reports","missing_unique_items":["dynamic voltage/current-loop implementation","integrators and anti-windup routing","measurement filters and exact sensor routing","PWM/SVPWM implementation","physical source controller","current guard implementation","complete execution schedule"]},
        "classification_legend":{"R":"recovered directly from supplied artifacts","P":"taken exactly from cited primary source","N":"newly selected design decision","V":"inferred and subsequently verified","U":"unresolved"},
        "recovered_R":{"plant":{"S_b_VA":app.plant.S_b_VA,"V_ll_rms_V":app.plant.V_ll_rms_V,"f_0_Hz":app.plant.f_0_Hz,"L_f_H":app.plant.L_f_H,"C_f_F":app.plant.C_f_F,"L_g_H":app.plant.L_g_H,"R_f_ohm":app.plant.R_f_ohm,"R_g_ohm":app.plant.R_g_ohm,"V_dc_star_V":app.plant.V_dc_star_V,"C_dc_F":app.plant.C_dc_F,"T_outer_nom_s":app.plant.T_outer_nom_s},"electrical_equilibrium_first_10_original":np.load(bundle)["eq"].tolist(),"static_map":"delta v_sw = N_v delta v_o - K_e col(delta i_f,delta v_c,delta i_g)","bundle":str(bundle),"bundle_sha256":sha256(bundle)},
        "primary_P":PRIMARY_REFERENCES,
        "new_N":{"dvoc_signal_choice":"v_o internal reference; i_o^dVOC=1.5 i_g; physical PCC power uses v_c and i_g","controller":asdict(app.controller),"converter_loss_model":{"p_loss_fixed_W":app.plant.p_loss_fixed_W,"R_loss_equiv_ohm":app.plant.R_loss_equiv_ohm},"measurement":asdict(app.measurement),"modulator":asdict(app.modulator),"source":asdict(app.source),"guard":asdict(app.guard),"timing":asdict(app.timing),"normal_action_bounds":ACTION_BOUNDS,"declared_normal_domain":val["declared_normal_domain"]},
        "verified_V":{"all_model_closure_checks_pass":val["all_model_closure_checks_pass"],"checks":val["checks"],"equilibrium":val["equilibrium"],"residuals":val["residuals"]},
        "unresolved_U":["hardware-identified uncertainty sets","switching/dead-time residual bound","source tracking-error experiment","hold-wide validated enclosure","robust controlled-invariant set","recursive feasibility","guard/outer composition theorem","target-processor worst-case timing"],
        "state_inventory":{"open_physical_state_order":["v_o_d","v_o_q","i_f_d","i_f_q","v_c_d","v_c_q","i_g_d","i_g_q","E_dc","p_s","E_s","p_r"],"continuous_hybrid_state_order":STATE_ORDER,"continuous_hybrid_state_units":STATE_UNITS,"timing_phase_states":["phi_fast_s","phi_source_s"],"guard_numeric_state":["release_timer_s"],"guard_discrete_modes":["guard_active","hardware_trip"],"queued_action_order":ACTION_ORDER,"queued_action_units":ACTION_UNITS,"dimensions":val["dimensions"]},
        "matrix_file":{"path":str(matrix_path),"sha256":sha256(matrix_path)},
        "generated_files":{k:{"path":str(v),"sha256":sha256(v)} for k,v in generated.items() if v.exists() and v!=path},
        "next_gates":{"B1_conference":["application-state enclosure","hold-wide physical constraint enclosure","present-hold reserve nu_H","queued-action one-step reserve nu_Q","unique projection","no recursive claim"],"B2_journal":["explicit nonempty robust control-invariant K","robust predecessor","viable-action reserve nu_V","recursive feasibility","continuous-time constraints across all holds","exact nominal noninterference"],"C_TPEL":["switching model","full-hardware experiment","native comparators","measured worst-case execution time","independent reproducibility rerun"]},
    }
    path.write_text(json.dumps(jsonable(manifest),indent=2),encoding="utf-8")


def main()->None:
    parser=argparse.ArgumentParser(); parser.add_argument("--bundle",type=Path,default=Path("/mnt/data/queued_dvoc_gate_candidate.npz")); parser.add_argument("--outdir",type=Path,default=Path("/mnt/data")); args=parser.parse_args(); args.outdir.mkdir(parents=True,exist_ok=True)
    app,oldNv,oldKe,oldA=build_apparatus(args.bundle)
    val,arrays=validate(app,oldNv,oldKe,oldA)
    matrix_path=args.outdir/"reference_apparatus_matrices.npz"; np.savez(matrix_path,**arrays)
    validation_path=args.outdir/"model_closure_validation.json"; validation_path.write_text(json.dumps(jsonable(val),indent=2),encoding="utf-8")
    controller_path=args.outdir/"controller_equations.md"; write_controller_equations(controller_path,app,val)
    measurement_path=args.outdir/"measurement_routing.csv"; write_measurement_csv(measurement_path,app)
    timing_path=args.outdir/"timing_diagram.md"; write_timing_diagram(timing_path,app)
    manifest_path=args.outdir/"reference_apparatus_manifest.json"
    generated={"reference_apparatus.py":Path(__file__).resolve(),"reference_apparatus_matrices.npz":matrix_path,"controller_equations.md":controller_path,"measurement_routing.csv":measurement_path,"timing_diagram.md":timing_path,"model_closure_validation.json":validation_path}
    write_manifest(manifest_path,app,val,matrix_path,generated,args.bundle)
    # add self path without self hash
    m=json.loads(manifest_path.read_text()); m["generated_files"]["reference_apparatus_manifest.json"]={"path":str(manifest_path),"sha256_note":"self hash intentionally omitted"}; manifest_path.write_text(json.dumps(m,indent=2))
    print(json.dumps({"decision":val["decision"],"all_model_closure_checks_pass":val["all_model_closure_checks_pass"],"failed_checks":[k for k,v in val["checks"].items() if not v],"state_dimension":val["dimensions"],"files":[str(Path(__file__).resolve()),str(matrix_path),str(manifest_path),str(controller_path),str(measurement_path),str(timing_path),str(validation_path)]},indent=2))


if __name__=="__main__":
    main()
