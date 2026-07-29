#!/usr/bin/env python3
"""Gate-A.1 corrected hybrid reference apparatus for queued-action dVOC.

This executable repairs the canonical apparatus without reusing any historical
certificate data.  It uses the recovered SI plant/equilibrium and the newly
selected cascaded controller from ``reference_apparatus.py``, but corrects the
hybrid implementation as follows:

* the PWM-held variable is the dimensionless modulation vector ``m_hold``;
* the bridge voltage is ``V_dc*m_hold/sqrt(3)`` plus explicit residual inputs;
* the source command is latched every 100 us and the source obeys the exact
  first-order inter-event dynamics;
* Gate B1 uses a strict guard-inactive normal mode; threshold crossing exits
  that mode, while a software trip request blocks the bridge and commands the
  source toward zero;
* one previous filtered sample is the complete sensing-delay model (the
  declared 2 us aperture and 8 us conversion fit inside that 50 us latency and
  are not added again);
* quantization, bounded sensor noise, timestamps, and filter states are
  explicit;
* sampled eigenvalues are converted through ``log(z)/T``.

The script performs Gate-A.1 model verification and a multirate phase/timing
sweep.  It does not construct a Gate-B1 safety certificate unless imported by
separate code after Gate-A.1 passes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
from scipy.linalg import eig, eigvals, expm

import reference_apparatus as base

Array = np.ndarray
J = base.J
I2 = np.eye(2)
SQRT3 = math.sqrt(3.0)
KAPPA_P = 1.5

# Corrected 32-state order.  Only the PWM hold coordinates differ from A.0.
STATE_ORDER = [
    "v_o_d", "v_o_q", "i_f_d", "i_f_q", "v_c_d", "v_c_q",
    "i_g_d", "i_g_q", "E_dc", "p_s", "E_s", "p_r",
    "xi_v_d", "xi_v_q", "xi_i_d", "xi_i_q", "x_ad_d", "x_ad_q",
    "hat_i_f_d", "hat_i_f_q", "hat_v_c_d", "hat_v_c_q",
    "hat_i_g_d", "hat_i_g_q", "hat_v_g_d", "hat_v_g_q",
    "hat_V_dc", "hat_p_s", "m_hold_d", "m_hold_q",
    "p_s_cmd_hold", "p_r_cmd_hold",
]
STATE_UNITS = [
    "V", "V", "A", "A", "V", "V", "A", "A", "J", "W", "J", "W",
    "V*s", "V*s", "A*s", "A*s", "A", "A", "A", "A", "V", "V",
    "A", "A", "V", "V", "V", "W", "1", "1", "W", "W",
]
ACTION_ORDER = ["sigma", "Delta_omega", "u_s"]
ACTION_UNITS = ["1/s", "rad/s", "W/s"]

VO=slice(0,2); IF=slice(2,4); VC=slice(4,6); IG=slice(6,8)
E_DC=8; P_S=9; E_S=10; P_R=11; XI_V=slice(12,14); XI_I=slice(14,16)
X_AD=slice(16,18); YF=slice(18,28); M_HOLD=slice(28,30); PS_CMD_HOLD=30; PR_CMD_HOLD=31
N_X=32; N_Q=3; N_Z=35

# Gate-B1 uses option B: strict guard-inactive domain.
I_B1_PU = 1.05

@dataclass
class HybridMode:
    gate_B1_exit: bool = False
    software_trip_request: bool = False
    release_timer_s: float = 0.0

@dataclass(frozen=True)
class NoiseBounds:
    current_A: float
    ac_voltage_V: float
    dc_voltage_V: float
    source_power_W: float


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):
            h.update(b)
    return h.hexdigest()


def jsonable(obj: Any) -> Any:
    if isinstance(obj,np.ndarray): return obj.tolist()
    if isinstance(obj,(np.integer,np.floating,np.bool_)): return obj.item()
    if isinstance(obj,complex): return {"real":obj.real,"imag":obj.imag}
    if isinstance(obj,dict): return {k:jsonable(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)): return [jsonable(v) for v in obj]
    return obj


def finite_difference_jacobian(fun: Callable[[Array],Array], x: Array, rel_step: float=1e-6) -> Array:
    x=np.asarray(x,dtype=float); y=np.asarray(fun(x),dtype=float)
    Jfd=np.empty((y.size,x.size))
    for j in range(x.size):
        h=rel_step*max(1.0,abs(float(x[j])))
        xp=x.copy(); xm=x.copy(); xp[j]+=h; xm[j]-=h
        Jfd[:,j]=(np.asarray(fun(xp))-np.asarray(fun(xm)))/(2*h)
    return Jfd


def continuous_pole_record(s: complex) -> dict[str,float]:
    mag=abs(s)
    return {"real_1_s":float(s.real),"imag_rad_s":float(s.imag),
            "frequency_Hz":float(abs(s.imag)/(2*math.pi)),
            "damping_ratio":float(-s.real/mag) if mag>0 else 0.0}


def discrete_pole_record(z: complex, T: float) -> dict[str,float]:
    if abs(z)==0:
        s=complex(-math.inf,0)
    else:
        s=np.log(complex(z))/T
    mag=abs(s) if math.isfinite(s.real) else math.inf
    return {"z_real":float(z.real),"z_imag":float(z.imag),"z_magnitude":float(abs(z)),
            "s_eq_real_1_s":float(s.real),"s_eq_imag_rad_s":float(s.imag),
            "frequency_Hz":float(abs(s.imag)/(2*math.pi)),
            "damping_ratio":float(-s.real/mag) if mag not in (0,math.inf) else 0.0}


def build_A1(bundle: Path) -> tuple[base.Apparatus,Array,Array,Array,Array]:
    app,oldNv,oldKe,oldA=base.build_apparatus(bundle)
    x=np.asarray(app.x_star,dtype=float).copy()
    Vdc=base.Vdc_from_energy(x[E_DC],app.plant)
    mstar=SQRT3*np.asarray(app.v_sw_star)/Vdc
    x[M_HOLD]=mstar
    # Replace immutable dataclass only at the tuple field.
    app=base.Apparatus(**{**asdict(app),"plant":app.plant,"dvoc":app.dvoc,
        "controller":app.controller,"measurement":app.measurement,"modulator":app.modulator,
        "source":app.source,"guard":app.guard,"timing":app.timing,"x_star":tuple(x.tolist())})
    return app,oldNv,oldKe,oldA,mstar


def bridge_voltage(x: Array, app: base.Apparatus,
                   d_deadtime: Array|None=None, d_switch: Array|None=None) -> Array:
    ddt=np.zeros(2) if d_deadtime is None else np.asarray(d_deadtime,dtype=float)
    dsw=np.zeros(2) if d_switch is None else np.asarray(d_switch,dtype=float)
    return base.Vdc_from_energy(float(x[E_DC]),app.plant)*np.asarray(x[M_HOLD])/SQRT3+ddt+dsw


def measurement_true(x: Array, app: base.Apparatus, v_g: Array|None=None) -> Array:
    vg=np.asarray(app.v_g_star if v_g is None else v_g,dtype=float)
    return np.r_[x[IF],x[VC],x[IG],vg,base.Vdc_from_energy(x[E_DC],app.plant),x[P_S]]


def quantization_steps(app: base.Apparatus) -> Array:
    m=app.measurement
    qi=2*m.current_full_scale_A/(2**m.adc_bits)
    qv=2*m.ac_voltage_full_scale_V/(2**m.adc_bits)
    qdc=m.dc_voltage_full_scale_V/(2**m.adc_bits)
    qp=2*m.source_power_full_scale_W/(2**m.adc_bits)
    return np.r_[np.full(2,qi),np.full(2,qv),np.full(2,qi),np.full(2,qv),qdc,qp]


def measurement_bounds(app: base.Apparatus) -> Array:
    m=app.measurement
    return np.r_[np.full(2,m.current_noise_bound_A),np.full(2,m.ac_voltage_noise_bound_V),
                 np.full(2,m.current_noise_bound_A),np.full(2,m.ac_voltage_noise_bound_V),
                 m.dc_voltage_noise_bound_V,m.source_power_noise_bound_W]


def measurement_sample(x: Array, app: base.Apparatus, noise: Array|None=None,
                       quantize: bool=True, v_g: Array|None=None) -> Array:
    y=measurement_true(x,app,v_g)
    if noise is not None:
        n=np.asarray(noise,dtype=float)
        b=measurement_bounds(app)
        if n.shape!=(10,) or np.any(np.abs(n)>b+1e-15):
            raise ValueError("measurement noise outside declared componentwise bounds")
        y=y+n
    if quantize:
        q=quantization_steps(app)
        y=np.round(y/q)*q
    return y


def dvoc_relative_field(v_o: Array, i_g_hat: Array, app: base.Apparatus) -> Array:
    return base.dvoc_relative_field(v_o,i_g_hat,app)


def intrinsic_action(v_o: Array, field: Array, app: base.Apparatus) -> Array:
    return base.intrinsic_action(v_o,field,app)


def nominal_action(x: Array, app: base.Apparatus) -> Array:
    y=x[YF]; i_f_hat=y[0:2]; i_g_hat=y[4:6]
    xi=intrinsic_action(x[VO],dvoc_relative_field(x[VO],i_g_hat,app),app)
    Ehat=0.5*app.plant.C_dc_F*float(y[8])**2
    v_sw_hat=float(y[8])*np.asarray(x[M_HOLD])/SQRT3
    p_load=KAPPA_P*float(v_sw_hat@i_f_hat)+base.loss_converter(i_f_hat,app.plant)
    us=-app.k_P_per_s*(float(y[9])-p_load)-app.k_E_per_s2*(Ehat-float(app.x_star[E_DC]))
    us=float(np.clip(us,-app.source.u_s_max_W_s,app.source.u_s_max_W_s))
    return np.r_[xi,us]


def controller_algebra(x: Array, app: base.Apparatus) -> dict[str,Array]:
    return base.controller_algebra(x,app)


def source_limits(x: Array, app: base.Apparatus) -> tuple[float,float,float]:
    s=app.source; Vs=base.Vsource_from_energy(float(x[E_S]),s)
    pcap=s.I_buffer_max_A*Vs
    return max(s.p_s_min_W,-pcap),min(s.p_s_max_W,pcap),Vs


def source_event_commands(x: Array, q: Array, app: base.Apparatus,
                          mode: HybridMode|None=None) -> tuple[float,float,dict[str,float]]:
    s=app.source; ps=float(x[P_S]); Es=float(x[E_S])
    pmin,pmax,Vs=source_limits(x,app)
    if mode is not None and mode.software_trip_request:
        return 0.0,0.0,{"V_source_V":Vs,"pmin_W":pmin,"pmax_W":pmax,"trip":1.0}
    ps_cmd=float(np.clip(ps+s.tau_s*float(q[2]),pmin,pmax))
    ls=base.loss_source(ps,Es,s)
    pr_cmd=float(np.clip(ps+ls+s.k_buffer_energy_per_s*(s.E_buffer_star_J-Es),
                         s.p_recharge_min_W,s.p_recharge_max_W))
    return ps_cmd,pr_cmd,{"V_source_V":Vs,"pmin_W":pmin,"pmax_W":pmax,"trip":0.0}


def source_interevent_exact(ps0: float, ps_cmd_hold: float, d_s_W: float,
                            tau: float, tau_s: float) -> float:
    a=math.exp(-float(tau)/float(tau_s))
    return float(ps_cmd_hold)+float(d_s_W)+(float(ps0)-float(ps_cmd_hold)-float(d_s_W))*a


def source_event_exact_map(ps_j: float, u_s: float, d_s_W: float, tau: float,
                           pmin: float, pmax: float, tau_s: float) -> tuple[float,float]:
    cmd=float(np.clip(float(ps_j)+tau_s*float(u_s),pmin,pmax))
    return cmd,source_interevent_exact(ps_j,cmd,d_s_W,tau,tau_s)


def fast_event(x: Array, q: Array, mode: HybridMode, app: base.Apparatus,
               *, noise: Array|None=None, quantize: bool=True,
               timestamp_s: float=0.0) -> tuple[Array,HybridMode,dict[str,float]]:
    """One 50-us controller/PWM/measurement event.

    Gate B1 uses option B from the audit: no current-barrier intervention is
    composed with the outer certificate.  Crossing the warning level exits
    the certified mode.  A software trip request blocks the bridge and is
    latched until manual reset.
    """
    xx=np.asarray(x,dtype=float).copy(); dt=app.timing.T_fast_s
    old_y=xx[YF].copy(); ni=float(np.linalg.norm(old_y[0:2])); Inom=app.plant.I_phase_peak_A
    new=HybridMode(mode.gate_B1_exit,mode.software_trip_request,mode.release_timer_s)
    if ni>=app.guard.I_warning_pu*Inom:
        new.gate_B1_exit=True
    if ni>=app.guard.I_trip_pu*Inom:
        new.software_trip_request=True; new.gate_B1_exit=True

    alg=controller_algebra(xx,app)
    Vhat=max(float(old_y[8]),1e-6)
    m_raw=SQRT3*np.asarray(alg["v_raw"])/Vhat
    m_applied=base.project_circle(m_raw,app.modulator.m_max)
    if new.software_trip_request:
        m_applied=np.zeros(2)
    v_applied_hat=Vhat*m_applied/SQRT3

    # Freeze PI integrators after a trip request; otherwise apply back-calculation.
    if not new.software_trip_request:
        ev=np.asarray(alg["e_v"]); iraw=np.asarray(alg["i_raw"]); iref=np.asarray(alg["i_ref"])
        ei=np.asarray(alg["e_i"]); vraw=np.asarray(alg["v_raw"])
        xx[XI_V]+=dt*(ev+(iref-iraw)/(app.K_Iv_A_per_Vs*app.controller.T_aw_voltage_s))
        xx[XI_I]+=dt*(ei+(v_applied_hat-vraw)/(app.K_Ii_ohm_per_s*app.controller.T_aw_current_s))
    i_c_meas=old_y[0:2]-old_y[4:6]
    a_ad=math.exp(-app.omega_ad*dt)
    xx[X_AD]=a_ad*xx[X_AD]+(1-a_ad)*i_c_meas
    xx[M_HOLD]=m_applied

    # Complete sensing-delay model: controller used old filtered sample above;
    # current acquisition is now quantized/noisy and feeds the exact filter
    # update for use at the next fast event.  The 2+8 us front-end latency is
    # contained inside this one-sample (50 us) latency, not added separately.
    ys=measurement_sample(xx,app,noise=noise,quantize=quantize)
    aa=np.r_[np.full(8,math.exp(-2*math.pi*app.measurement.f_ac_filter_Hz*dt)),
             np.full(2,math.exp(-2*math.pi*app.measurement.f_dc_filter_Hz*dt))]
    xx[YF]=aa*old_y+(1-aa)*ys
    return xx,new,{"timestamp_s":float(timestamp_s),"i_f_filtered_A":ni,
                   "m_raw_norm":float(np.linalg.norm(m_raw)),
                   "m_hold_norm":float(np.linalg.norm(m_applied)),
                   "gate_B1_exit":float(new.gate_B1_exit),
                   "software_trip_request":float(new.software_trip_request)}


def source_event(x: Array, q: Array, mode: HybridMode, app: base.Apparatus) -> tuple[Array,dict[str,float]]:
    xx=np.asarray(x,dtype=float).copy()
    ps,pr,info=source_event_commands(xx,q,app,mode)
    xx[PS_CMD_HOLD]=ps; xx[PR_CMD_HOLD]=pr
    return xx,info


def physical_rhs(x: Array, q: Array, app: base.Apparatus,
                 *, source_disturbance_W: float=0.0, v_g: Array|None=None,
                 d_deadtime: Array|None=None,d_switch: Array|None=None) -> Array:
    pp=app.plant; s=app.source; vg=np.asarray(app.v_g_star if v_g is None else v_g,dtype=float)
    xx=np.asarray(x,dtype=float); qq=np.asarray(q,dtype=float); d=np.zeros_like(xx)
    v_sw=bridge_voltage(xx,app,d_deadtime,d_switch)
    d[VO]=qq[0]*xx[VO]+qq[1]*(J@xx[VO])
    d[IF]=(v_sw-xx[VC]-pp.R_f_ohm*xx[IF]-pp.omega_0*pp.L_f_H*(J@xx[IF]))/pp.L_f_H
    d[VC]=(xx[IF]-xx[IG]-pp.omega_0*pp.C_f_F*(J@xx[VC]))/pp.C_f_F
    d[IG]=(xx[VC]-vg-pp.R_g_ohm*xx[IG]-pp.omega_0*pp.L_g_H*(J@xx[IG]))/pp.L_g_H
    d[E_DC]=xx[P_S]-KAPPA_P*float(v_sw@xx[IF])-base.loss_converter(xx[IF],pp)
    d[P_S]=(-xx[P_S]+xx[PS_CMD_HOLD]+float(source_disturbance_W))/s.tau_s
    ls=base.loss_source(xx[P_S],xx[E_S],s)
    d[E_S]=xx[P_R]-xx[P_S]-ls
    d[P_R]=(-xx[P_R]+xx[PR_CMD_HOLD])/s.tau_recharge_s
    return d


def integrate_physical(x: Array,q:Array,dt:float,app:base.Apparatus,nsub:int|None=None,
                       source_disturbance_W:float=0.0) -> Array:
    xx=np.asarray(x,dtype=float).copy(); n=max(1,int(math.ceil(dt/10e-6))) if nsub is None else max(1,nsub); h=dt/n
    for _ in range(n):
        f=lambda z:physical_rhs(z,q,app,source_disturbance_W=source_disturbance_W)
        k1=f(xx); k2=f(xx+.5*h*k1); k3=f(xx+.5*h*k2); k4=f(xx+h*k3)
        xx+=h*(k1+2*k2+2*k3+k4)/6
    return xx


def hold_transition(x:Array,q:Array,T:float,phase_fast:float,phase_source:float,
                    mode:HybridMode,app:base.Apparatus,*,quantize:bool=True,
                    noise_schedule:Callable[[float],Array|None]|None=None,
                    source_disturbance_W:float=0.0) -> tuple[Array,float,float,HybridMode,dict[str,Any]]:
    xx=np.asarray(x,dtype=float).copy(); t=0.0; pf=float(phase_fast); ps=float(phase_source)
    tol=2e-14; events=[]; max_if=float(np.linalg.norm(xx[IF])); max_ig=float(np.linalg.norm(xx[IG])); minV=base.Vdc_from_energy(xx[E_DC],app.plant); maxm=float(np.linalg.norm(xx[M_HOLD]))
    while t<T-tol:
        tf=app.timing.T_fast_s-pf if pf>tol else app.timing.T_fast_s
        ts=app.timing.T_source_s-ps if ps>tol else app.timing.T_source_s
        dt=min(tf,ts,T-t)
        xx=integrate_physical(xx,q,dt,app,source_disturbance_W=source_disturbance_W)
        t+=dt; pf+=dt; ps+=dt
        max_if=max(max_if,float(np.linalg.norm(xx[IF]))); max_ig=max(max_ig,float(np.linalg.norm(xx[IG]))); minV=min(minV,base.Vdc_from_energy(xx[E_DC],app.plant)); maxm=max(maxm,float(np.linalg.norm(xx[M_HOLD])))
        f_due=abs(pf-app.timing.T_fast_s)<5e-13 or pf>app.timing.T_fast_s
        s_due=abs(ps-app.timing.T_source_s)<5e-13 or ps>app.timing.T_source_s
        if f_due:
            nz=None if noise_schedule is None else noise_schedule(t)
            xx,mode,info=fast_event(xx,q,mode,app,noise=nz,quantize=quantize,timestamp_s=t)
            events.append((round(t,12),"F")); pf=0.0
        if s_due:
            xx,_=source_event(xx,q,mode,app); events.append((round(t,12),"S")); ps=0.0
    return xx,pf,ps,mode,{"events":events,"max_i_f_A":max_if,"max_i_g_A":max_ig,
                            "min_Vdc_V":minV,"max_m_norm":maxm,
                            "gate_B1_exit":mode.gate_B1_exit,
                            "software_trip_request":mode.software_trip_request}


def augmented_transition(x:Array,q:Array,u:Array,T:float,phase_fast:float,phase_source:float,
                         mode:HybridMode,app:base.Apparatus,**kwargs:Any):
    xn,pf,ps,m,st=hold_transition(x,q,T,phase_fast,phase_source,mode,app,**kwargs)
    return xn,pf,ps,np.asarray(u,dtype=float).copy(),m,st


def flow_linearization(app:base.Apparatus) -> tuple[Array,Array]:
    x0=np.asarray(app.x_star); q0=np.zeros(3)
    A=finite_difference_jacobian(lambda x:physical_rhs(x,q0,app),x0,rel_step=2e-7)
    B=finite_difference_jacobian(lambda q:physical_rhs(x0,q,app),q0,rel_step=2e-7)
    return A,B


def event_linearizations(app:base.Apparatus) -> tuple[Array,Array,Array]:
    x0=np.asarray(app.x_star); q0=np.zeros(3); z0=np.r_[x0,q0]
    def ff(z:Array)->Array:
        x, q=z[:N_X],z[N_X:]
        xn,_,_=fast_event(x,q,HybridMode(),app,quantize=False,noise=None)
        return np.r_[xn,q]
    def fs(z:Array)->Array:
        x,q=z[:N_X],z[N_X:]
        xn,_=source_event(x,q,HybridMode(),app)
        return np.r_[xn,q]
    JF=finite_difference_jacobian(ff,z0,rel_step=3e-7)
    JS=finite_difference_jacobian(fs,z0,rel_step=3e-7)
    K=finite_difference_jacobian(lambda x:nominal_action(x,app),x0,rel_step=3e-7)
    return JF,JS,K


class LinearHybridFamily:
    def __init__(self,app:base.Apparatus):
        self.app=app; A,B=flow_linearization(app); self.A=A; self.B=B
        self.Az=np.zeros((N_Z,N_Z)); self.Az[:N_X,:N_X]=A; self.Az[:N_X,N_X:]=B
        self.JF,self.JS,self.Knom=event_linearizations(app)
        self._flow_cache:dict[float,Array]={}
    def flow(self,dt:float)->Array:
        key=round(float(dt),12)
        if key not in self._flow_cache: self._flow_cache[key]=expm(self.Az*key)
        return self._flow_cache[key]
    def map(self,T:float,pf:float,ps:float)->tuple[Array,str,list[tuple[float,str]]]:
        M=np.eye(N_Z); t=0.0; tol=2e-14; events=[]; pf=float(pf); ps=float(ps)
        while t<T-tol:
            tf=self.app.timing.T_fast_s-pf if pf>tol else self.app.timing.T_fast_s
            ts=self.app.timing.T_source_s-ps if ps>tol else self.app.timing.T_source_s
            dt=min(tf,ts,T-t); M=self.flow(dt)@M; t+=dt; pf+=dt; ps+=dt
            fd=abs(pf-self.app.timing.T_fast_s)<5e-13 or pf>self.app.timing.T_fast_s
            sd=abs(ps-self.app.timing.T_source_s)<5e-13 or ps>self.app.timing.T_source_s
            if fd: M=self.JF@M; events.append((round(t,12),"F")); pf=0.0
            if sd: M=self.JS@M; events.append((round(t,12),"S")); ps=0.0
        F=np.zeros((N_Z,N_Z)); F[:N_X,:]=M[:N_X,:]; F[N_X:,:N_X]=self.Knom
        sig="".join(e for _,e in events)
        return F,sig,events


def critical_mode(F:Array,T:float,labels:list[str]) -> dict[str,Any]:
    vals,vl,vr=eig(F,left=True,right=True); idx=int(np.argmax(np.abs(vals))); z=vals[idx]
    v=vr[:,idx]; w=vl[:,idx]
    denom=np.vdot(w,v)
    if abs(denom)>1e-20: w=w/np.conj(denom)
    part=np.abs(w*v); part=part/(part.sum() if part.sum()>0 else 1)
    order=np.argsort(part)[::-1][:10]
    res=np.linalg.norm(F@v-z*v)/(max(1.0,np.linalg.norm(F)*np.linalg.norm(v)))
    rec=discrete_pole_record(z,T)
    rec.update({"spectral_radius":float(abs(z)),"eigen_residual_relative":float(res),
                "participation":[{"state":labels[i],"fraction":float(part[i]),
                                  "right_abs":float(abs(v[i])),"left_abs":float(abs(w[i]))} for i in order]})
    return rec


def phase_sweep(app:base.Apparatus,out_csv:Path,grid_us:float=1.0)->dict[str,Any]:
    fam=LinearHybridFamily(app); labels=STATE_ORDER+["q_sigma","q_Delta_omega","q_u_s"]
    Tvals=np.arange(495.0,505.0+0.1,1.0)*1e-6
    pfvals=np.arange(0.0,50.0-0.1,grid_us)*1e-6
    psvals=np.arange(0.0,100.0-0.1,grid_us)*1e-6
    worst=None; sigs={}; rows=[]
    for T in Tvals:
        for pf in pfvals:
            for ps in psvals:
                F,sig,ev=fam.map(float(T),float(pf),float(ps)); vals=eigvals(F); rho=float(np.max(np.abs(vals))); idx=int(np.argmax(np.abs(vals))); z=vals[idx]; s=np.log(complex(z))/float(T)
                row={"T_us":T*1e6,"phi_fast_us":pf*1e6,"phi_source_us":ps*1e6,
                     "spectral_radius":rho,"critical_z_real":float(z.real),"critical_z_imag":float(z.imag),
                     "s_eq_real_1_s":float(s.real),"s_eq_imag_rad_s":float(s.imag),
                     "frequency_Hz":float(abs(s.imag)/(2*math.pi)),
                     "damping_ratio":float(-s.real/abs(s)) if abs(s)>0 else 0.0,
                     "event_signature":sig,"event_count":len(ev)}
                rows.append(row); sigs[sig]=sigs.get(sig,0)+1
                if worst is None or rho>worst[0]: worst=(rho,T,pf,ps,F,sig,ev)
    fields=list(rows[0].keys())
    with out_csv.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    assert worst is not None
    rho,T,pf,ps,F,sig,ev=worst

    # Sub-microsecond local refinement around the worst integer-grid point.
    # This is a numerical closure test, not a verified continuum contraction theorem.
    T0,pf0,ps0=float(T),float(pf),float(ps)
    for TT in np.arange(max(495e-6,T0-1e-6),min(505e-6,T0+1e-6)+1e-13,0.1e-6):
        for pff in np.arange(max(0.0,pf0-1e-6),min(50e-6-1e-13,pf0+1e-6)+1e-13,0.1e-6):
            for pss in np.arange(max(0.0,ps0-1e-6),min(100e-6-1e-13,ps0+1e-6)+1e-13,0.1e-6):
                FF,ssig,eev=fam.map(float(TT),float(pff),float(pss)); rr=float(np.max(np.abs(eigvals(FF))))
                if rr>rho: rho,T,pf,ps,F,sig,ev=rr,float(TT),float(pff),float(pss),FF,ssig,eev
    crit=critical_mode(F,float(T),labels)
    return {"grid_spacing_us":grid_us,"grid_rows":len(rows),"T_values_us":[495,505,1],
            "phase_fast_range_us":[0,50],"phase_source_range_us":[0,100],
            "distinct_event_signatures":len(sigs),"event_signature_counts":sigs,
            "worst":{"T_us":T*1e6,"phi_fast_us":pf*1e6,"phi_source_us":ps*1e6,
                     "event_signature":sig,"events":[[t*1e6,e] for t,e in ev],**crit},
            "continuous_search":{"method":"0.1-us local refinement around worst integer-grid point",
                                 "spectral_radius":rho,"T_us":T*1e6,
                                 "phi_fast_us":pf*1e6,"phi_source_us":ps*1e6},
            "linear_matrices":{"A_flow":fam.A,"B_q_flow":fam.B,"J_fast":fam.JF,"J_source":fam.JS,"K_nominal":fam.Knom,"F_worst":F}}


def normalized_scales(app:base.Apparatus)->Array:
    x0=np.asarray(app.x_star); pp=app.plant; s=app.source
    return np.r_[np.full(2,.05*app.V_star_V),np.full(2,.1*pp.I_phase_peak_A),
                 np.full(2,.05*app.V_star_V),np.full(2,.1*pp.I_phase_peak_A),
                 .05*x0[E_DC],.1*pp.S_b_VA,.05*x0[E_S],.1*pp.S_b_VA,
                 np.full(2,.02*app.V_star_V),np.full(2,.02*pp.I_phase_peak_A),
                 np.full(2,.1*pp.I_phase_peak_A),np.full(2,.1*pp.I_phase_peak_A),
                 np.full(2,.05*app.V_star_V),np.full(2,.1*pp.I_phase_peak_A),
                 np.full(2,.05*app.V_star_V),.05*pp.V_dc_star_V,.1*pp.S_b_VA,
                 np.full(2,.1),.1*pp.S_b_VA,.1*pp.S_b_VA]


def deviation_norm(x:Array,app:base.Apparatus)->float:
    return float(np.linalg.norm((np.asarray(x)-np.asarray(app.x_star))/normalized_scales(app))/math.sqrt(N_X))


def simulate_closed_loop(app:base.Apparatus,x0:Array,Tend:float,pf:float,ps:float,
                         *,quantize:bool=False,noise_schedule:Callable[[float],Array|None]|None=None,
                         source_disturbance_W:float=0.0) -> dict[str,Any]:
    x=np.asarray(x0,dtype=float).copy(); q=np.zeros(3); u=nominal_action(x,app); mode=HybridMode(); t=0.0
    n0=deviation_norm(x,app); nmax=n0; max_if=0.; max_ig=0.; minV=1e99; maxm=0.; exits=0
    while t<Tend-1e-14:
        # One-update queue: current q acts; current snapshot computes next u.
        unext=nominal_action(x,app)
        T=min(app.timing.T_outer_nom_s,Tend-t)
        x,pf,ps,qnext,mode,st=augmented_transition(x,q,unext,T,pf,ps,mode,app,
                            quantize=quantize,noise_schedule=noise_schedule,
                            source_disturbance_W=source_disturbance_W)
        q=qnext; t+=T; nmax=max(nmax,deviation_norm(x,app)); max_if=max(max_if,st["max_i_f_A"]); max_ig=max(max_ig,st["max_i_g_A"]); minV=min(minV,st["min_Vdc_V"]); maxm=max(maxm,st["max_m_norm"]); exits+=int(st["gate_B1_exit"])
    return {"initial_normalized_deviation":n0,"final_normalized_deviation":deviation_norm(x,app),
            "max_normalized_deviation":nmax,"final_ratio":deviation_norm(x,app)/max(n0,1e-15),
            "max_i_f_A":max_if,"max_i_g_A":max_ig,"min_Vdc_V":minV,"max_m_norm":maxm,
            "gate_B1_exit_count":exits,"software_trip_request":mode.software_trip_request,
            "final_state":x.tolist(),"final_q":q.tolist()}


def perturbation_tests(app:base.Apparatus,worst_phase:dict[str,Any])->dict[str,Any]:
    xeq=np.asarray(app.x_star); tests={}
    cases:dict[str,Callable[[Array],None]]={
        "voltage_magnitude":lambda x:x.__setitem__(VO,x[VO]*1.01),
        "phase":lambda x:x.__setitem__(VO,base.rotation(math.radians(1.0))@x[VO]),
        "i_f":lambda x:x.__setitem__(IF,x[IF]+np.array([2.0,-1.0])),
        "i_g":lambda x:x.__setitem__(IG,x[IG]+np.array([2.0,1.0])),
        "V_dc":lambda x:x.__setitem__(E_DC,.5*app.plant.C_dc_F*(app.plant.V_dc_star_V+5.0)**2),
        "source_power":lambda x:x.__setitem__(P_S,x[P_S]+500.0),
        "source_buffer_energy":lambda x:x.__setitem__(E_S,.5*app.source.C_buffer_F*(app.source.V_buffer_star_V+5.0)**2),
        "integrators":lambda x:(x.__setitem__(XI_V,x[XI_V]+np.array([.05,-.03])),x.__setitem__(XI_I,x[XI_I]+np.array([.002,-.001]))),
        "filter_states":lambda x:x.__setitem__(YF,x[YF]+np.r_[.2,-.2,.25,-.25,.2,-.2,.25,-.25,.5,100.0]),
    }
    pf=float(worst_phase["phi_fast_us"])*1e-6; ps=float(worst_phase["phi_source_us"])*1e-6
    for name,mut in cases.items():
        x=xeq.copy(); mut(x); tests[name]=simulate_closed_loop(app,x,.12,pf,ps,quantize=False)
    # Clock phase test uses a common small physical perturbation at the worst phase.
    x=xeq.copy(); x[VC]+=np.array([1.0,-.5]); tests["clock_phases"]=simulate_closed_loop(app,x,.12,pf,ps,quantize=False)
    # Bounded quantization/noise test at equilibrium: alternating componentwise extrema.
    b=measurement_bounds(app); noise=lambda t: b*np.where(np.arange(10)%2==0,1.0,-1.0)
    tests["noise_quantization_bounded"]=simulate_closed_loop(app,xeq.copy(),.05,pf,ps,quantize=True,noise_schedule=noise)
    return tests


def polar_dvoc_audit(app:base.Apparatus)->dict[str,Any]:
    V=app.V_star_V; P=app.P_star_W; Q=app.Q_star_var; k=app.kappa_rad; eta=app.eta_ohm_per_s; alpha=app.alpha_S
    c=math.cos(k); s=math.sin(k); Astar=c*P+s*Q
    # Exact polar linearizations of the selected foundational dVOC law.
    df_dP_Hz_per_W=-eta*s/(2*math.pi*V**2)
    df_dQ_Hz_per_var=eta*c/(2*math.pi*V**2)
    dV_dQ_V_per_var=s*V/(2*(Astar-alpha*V**2))
    radial_decay=2*eta*(alpha-Astar/V**2)
    target_df=-app.dvoc.full_power_droop_Hz/app.plant.S_b_VA
    target_dV=-app.dvoc.voltage_droop_pu*V/app.dvoc.reactive_design_var
    return {"polar_equations":{"sigma":"eta[(cP*+sQ*)/V*^2-(cp+sq)/r^2+alpha(1-r^2/V*^2)]",
                                "Delta_omega":"eta[(sP*-cQ*)/V*^2+(cq-sp)/r^2]"},
            "derived":{"df_dP_Hz_per_W":df_dP_Hz_per_W,"df_dQ_Hz_per_var":df_dQ_Hz_per_var,
                       "dV_dQ_V_per_var":dV_dQ_V_per_var,"radial_decay_rate_1_s":radial_decay,
                       "radial_time_constant_s":1/radial_decay},
            "declared_targets":{"df_dP_Hz_per_W":target_df,"dV_dQ_V_per_var":target_dV},
            "relative_error":{"active_droop":abs((df_dP_Hz_per_W-target_df)/target_df),
                              "reactive_droop":abs((dV_dQ_V_per_var-target_dV)/target_dV)},
            "PEDG_2024_comparison":"The accessible primary metadata verifies a unified prescribed-droop parameterization and CHIL validation, but does not expose its equations. Equation-by-equation identity is therefore not claimed; the selected gains are independently verified against the exact polar equations."}


def validate_A1(app:base.Apparatus,phase:dict[str,Any]) -> tuple[dict[str,Any],dict[str,Array]]:
    x0=np.asarray(app.x_star); q0=np.zeros(3); pp=app.plant; s=app.source
    rhs=physical_rhs(x0,q0,app); vsw=bridge_voltage(x0,app)
    r_if=vsw-x0[VC]-pp.R_f_ohm*x0[IF]-pp.omega_0*pp.L_f_H*(J@x0[IF])
    r_vc=x0[IF]-x0[IG]-pp.omega_0*pp.C_f_F*(J@x0[VC])
    r_ig=x0[VC]-np.asarray(app.v_g_star)-pp.R_g_ohm*x0[IG]-pp.omega_0*pp.L_g_H*(J@x0[IG])
    dc=x0[P_S]-KAPPA_P*float(vsw@x0[IF])-base.loss_converter(x0[IF],pp)
    src=x0[P_R]-x0[P_S]-base.loss_source(x0[P_S],x0[E_S],s)
    # Analytic/numerical continuous flow matrices via independent finite differences at two steps.
    A,B=flow_linearization(app)
    A2=finite_difference_jacobian(lambda x:physical_rhs(x,q0,app),x0,rel_step=4e-7)
    B2=finite_difference_jacobian(lambda q:physical_rhs(x0,q,app),q0,rel_step=4e-7)
    relA=np.linalg.norm(A-A2,np.inf)/max(1,np.linalg.norm(A,np.inf)); relB=np.linalg.norm(B-B2,np.inf)/max(1,np.linalg.norm(B,np.inf))
    # Event causality and exact source map.
    xF,mF,iF=fast_event(x0,q0,HybridMode(),app,quantize=False)
    xS,infoS=source_event(x0,q0,HybridMode(),app)
    pmin,pmax,_=source_limits(x0,app); cmd,pex=source_event_exact_map(x0[P_S],100000.0,100.0,s.T_source_update_s,pmin,pmax,s.tau_s)
    pnum=source_interevent_exact(x0[P_S],cmd,100.0,s.T_source_update_s,s.tau_s)
    # Modulation scaling with Vdc.
    m=x0[M_HOLD].copy(); volt=[]
    for V in [810.,900.,990.]:
        xx=x0.copy(); xx[E_DC]=.5*pp.C_dc_F*V**2; volt.append({"Vdc_V":V,"v_sw_norm_V":float(np.linalg.norm(bridge_voltage(xx,app))),"expected_norm_V":float(V*np.linalg.norm(m)/SQRT3)})
    # Delay routing and loss consistency.
    y0=x0[YF].copy(); xt=x0.copy(); xt[IF]+=np.array([1.,0.]); xf,_,_=fast_event(xt,q0,HybridMode(),app,quantize=False)
    controller_used_previous=bool(np.allclose(controller_algebra(xt,app)["v_raw"],controller_algebra(np.r_[xt[:18],y0,xt[28:]],app)["v_raw"]))
    pl=base.loss_converter(x0[IF],pp); pl_formula=pp.p_loss_fixed_W+1.5*pp.R_loss_equiv_ohm*float(x0[IF]@x0[IF])
    # Trip behavior.
    xtrip=x0.copy(); xtrip[YF][0:2]=np.array([app.guard.I_trip_pu*pp.I_phase_peak_A+1,0]); xt2,mt,_=fast_event(xtrip,q0,HybridMode(),app,quantize=False); xt3,_=source_event(xt2,q0,mt,app)
    # dVOC equilibrium and units.
    xi=nominal_action(x0,app)
    polar=polar_dvoc_audit(app)
    pert=perturbation_tests(app,phase["worst"])
    # The nonlinear perturbation gate accepts either convergence or bounded return
    # inside the declared smooth normal domain.  A final/initial ratio is not a
    # valid criterion when the initial perturbation lives primarily in a lightly
    # weighted controller state and excites a nonnormal transient.
    I_B1=I_B1_PU*pp.I_phase_peak_A
    return_tests={k:(not v["software_trip_request"] and v["gate_B1_exit_count"]==0
                     and v["max_m_norm"]<=app.modulator.m_max+1e-10
                     and v["max_i_f_A"]<I_B1 and v["max_i_g_A"]<I_B1
                     and v["max_normalized_deviation"]<0.75)
                  for k,v in pert.items() if k!="noise_quantization_bounded"}
    nv=pert["noise_quantization_bounded"]
    noise_ok=(not nv["software_trip_request"] and nv["gate_B1_exit_count"]==0
              and nv["max_m_norm"]<=app.modulator.m_max+1e-10
              and nv["max_i_f_A"]<I_B1 and nv["max_i_g_A"]<I_B1
              and nv["max_normalized_deviation"]<0.05)
    # Gate A1 pass requires sampled normal branch stable over the numerical search.
    rho=phase["worst"]["spectral_radius"]
    checks={
        "dimensional_state_inventory":len(STATE_ORDER)==len(STATE_UNITS)==N_X,
        "physical_equilibrium":float(np.max(np.abs(rhs)))<1e-7,
        "ac_dc_source_balances":max(np.linalg.norm(r_if,np.inf),np.linalg.norm(r_vc,np.inf),np.linalg.norm(r_ig,np.inf),abs(dc),abs(src))<1e-7,
        "analytic_numeric_flow_jacobian":relA<2e-7 and relB<2e-7,
        "exact_event_causality":float(np.max(np.abs(xF-x0)))<1e-8 and float(np.max(np.abs(xS-x0)))<1e-8,
        "exact_source_event_map":abs(pex-pnum)<1e-12,
        "held_modulation_consistency":max(abs(v["v_sw_norm_V"]-v["expected_norm_V"]) for v in volt)<1e-10,
        "sensing_routing_previous_sample":controller_used_previous and float(np.linalg.norm(xf[YF]-y0))>0,
        "loss_model_consistency":abs(pl-pl_formula)<1e-12,
        "software_trip_behavior":mt.software_trip_request and mt.gate_B1_exit and np.linalg.norm(xt2[M_HOLD])<1e-15 and xt3[PS_CMD_HOLD]==0 and xt3[PR_CMD_HOLD]==0,
        "gate_B1_guard_inactive_at_equilibrium":not mF.gate_B1_exit,
        "nominal_dvoc_equilibrium":float(np.linalg.norm(xi))<1e-9,
        "action_and_slew_units":base.ACTION_BOUNDS["absolute"]["u_s_W_s"]==1_000_000.0 and base.ACTION_BOUNDS["one_update_slew"]["u_s_W_s"]==500_000.0,
        "hybrid_phase_sweep_local_stability":rho<1.0,
        # The task requires either exhaustive continuous phase enumeration or a
        # verified set-valued/polytopic contraction enclosure.  The supplied
        # artifact performs a dense finite grid and sub-microsecond refinement,
        # which is strong validation but not a continuum certificate.
        "continuous_timing_phase_contraction_certificate":False,
        "perturbation_bounded_return":all(return_tests.values()),
        "bounded_noise_quantization":noise_ok,
    }
    decision="A. GATE-A.1 PASSED" if all(checks.values()) else "B. GATE-A.1 FAILED — CORRECTABLE"
    arrays={"x_star_32":x0,"q_star_3":q0,"A_flow_32":A,"B_q_flow_32x3":B,
            "m_star_2":x0[M_HOLD],"v_sw_star_2":vsw,"measurement_quantization_steps_10":quantization_steps(app),
            "measurement_noise_bounds_10":measurement_bounds(app),
            **phase["linear_matrices"]}
    result={"decision":decision,"all_checks_pass":all(checks.values()),"checks":checks,
            "failed_checks":[k for k,v in checks.items() if not v],"state_order":STATE_ORDER,"state_units":STATE_UNITS,
            "equilibrium_residuals":{"rhs_inf":float(np.max(np.abs(rhs))),"if_V_inf":float(np.linalg.norm(r_if,np.inf)),"vc_A_inf":float(np.linalg.norm(r_vc,np.inf)),"ig_V_inf":float(np.linalg.norm(r_ig,np.inf)),"dc_power_W":float(dc),"source_power_W":float(src)},
            "jacobian":{"relative_A":float(relA),"relative_B":float(relB)},
            "source_event_map":{"ps_initial_W":float(x0[P_S]),"u_s_W_s":100000.0,"d_s_W":100.0,"command_W":cmd,"exact_next_W":pex,"residual_W":float(pex-pnum)},
            "modulation_scaling":volt,"loss":{"equilibrium_W":pl,"formula_W":pl_formula},
            "sensing":{"delay_model":"one previous 50-us filtered sample includes the 2-us aperture and 8-us conversion/frame latency; no additional 10-us transport delay is added","quantization_steps":quantization_steps(app),"noise_bounds":measurement_bounds(app)},
            "trip":{"name":"software_trip_request","physical_action":"m_hold=0; voltage/current PI integrators frozen; p_s_cmd_hold=p_r_cmd_hold=0 at the next source event; manual reset only","gate_B1_exits_on_warning":True},
            "guard":{"Gate_B1_option":"B: guard activation excluded from certified mode","I_B1_A":I_B1_PU*pp.I_phase_peak_A,"I_warning_A":app.guard.I_warning_pu*pp.I_phase_peak_A,"composition_claimed":False},
            "sampled_dvoc":{"terminology":"foundational dVOC field generates the nominal intrinsic action; the action is sampled at t_k, queued, and held during the next outer interval","polar_audit":polar},
            "hybrid_phase_sweep":{k:v for k,v in phase.items() if k!="linear_matrices"},
            "perturbation_tests":pert,"perturbation_pass":return_tests,"historical_certificate_reused":False,
            "scope":{"Gate_B1_started":False,"recursive_feasibility_claimed":False,"reason_B1_not_started":"Gate A.1 lacks a verified continuum timing/phase contraction enclosure"}}
    return result,arrays


def write_corrections(path:Path,result:dict[str,Any],app:base.Apparatus)->None:
    p=result["sampled_dvoc"]["polar_audit"]
    text=f"""# Gate-A.1 model corrections and independent verification

## Scientific identity

The controller is a **sampled-data implementability governor acting on the intrinsic command generated by nominal dVOC**. The foundational continuous dVOC field supplies the nominal intrinsic action. The action is sampled at the outer update, queued for one update, and held over the next outer interval. No continuous-time dVOC stability result is transferred automatically.

## Corrections applied

1. **Held modulation:** `m_hold` replaces `v_sw_hold`; the averaged bridge voltage is
   
   `v_sw = V_dc m_hold / sqrt(3) + d_deadtime + d_switch`.
2. **Source event:** `p_s_cmd` is recomputed only at each 100-us source event and held. Between events, `tau_s dot p_s=-p_s+p_s_cmd+d_s`; the exact map is implemented and tested.
3. **Current protection:** Gate B1 uses option B. The guard is excluded from the certified normal mode. Crossing {app.guard.I_warning_pu:.2f} pu exits that mode. A `software_trip_request` blocks the bridge (`m_hold=0`), freezes PI integrators, and commands both source channels toward zero at the next source event. Re-entry is manual only.
4. **Causal sensing:** the prior filtered sample is the complete 50-us sensing latency. The 2-us aperture and 8-us conversion/frame time fit inside it and are not counted again. Quantization, bounded noise, timestamped samples, and exact first-order filter updates are explicit.
5. **Sampled poles:** every discrete eigenvalue is reported through `s_eq=log(z)/T`.
6. **Loss:** all code and reports use `p_loss=P0+(3/2)R_loss||i_f||^2` under the amplitude-invariant Clarke convention.
7. **Sampled dVOC:** the exact polar linearizations verify the selected gain targets. Active-power/frequency droop relative error: {p['relative_error']['active_droop']:.3e}; reactive-power/voltage droop relative error: {p['relative_error']['reactive_droop']:.3e}. The exact local radial decay is {p['derived']['radial_decay_rate_1_s']:.6g} 1/s, not simply `2 eta alpha` at nonzero P*,Q*.
8. **Historical certificate:** no historical P, K_b, ellipsoid, timing gain, nonlinear coefficient, or falsifier enters this apparatus or this decision.

## Gate decision

**{result['decision']}**

Failed checks: {', '.join(result['failed_checks']) if result['failed_checks'] else 'none'}.

## Gate-B1 boundary

Gate B1 may start only if Gate A.1 passes. Guard activation, source saturation, modulation overrun, or software trip is an exit from the future Gate-B1 certified mode and is not certified recovery.
"""
    path.write_text(text,encoding="utf-8")


def write_manifest(path:Path,app:base.Apparatus,result:dict[str,Any],matrix_path:Path,csv_path:Path,script_path:Path)->None:
    m={"decision":result["decision"],"fixed_identity":"sampled-data implementability governor acting on the intrinsic command generated by nominal dVOC",
       "provenance":{"recovered":["100-kVA SI LCL/DC plant","electrical equilibrium","historical static map used only as audit reference"],
                     "published":["foundational dVOC vector law, APEC 2019 DOI 10.1109/APEC.2019.8722028","SVPWM held-modulation physics"],
                     "new_design":["cascaded controller","10-channel measurement architecture","programmable source/buffer","strict guard-inactive Gate-B1 domain","software trip action"],
                     "analytically_certified":["exact source inter-event map","bridge modulation relation","loss equation","polar dVOC droop mapping","retained-set algebra not addressed in A1"],
                     "numerically_validated":["equilibrium and balances","Jacobian","hybrid phase/timing sweep","perturbation return","trip behavior"],
                     "unresolved":["validated hold-wide uncertainty enclosure","Gate-B1 two-reserve certificate","hardware source mismatch bound","dead-time/switching residual identification","recursive feasibility"]},
       "state_order":STATE_ORDER,"state_units":STATE_UNITS,"action_order":ACTION_ORDER,"action_units":ACTION_UNITS,
       "dimensions":{"continuous_hybrid":N_X,"queued_action":N_Q,"sampled_physical_action":N_Z,"clock_phases":2,"discrete_mode_flags":2},
       "corrections":{"held_variable":"m_hold","bridge_relation":"v_sw=V_dc*m_hold/sqrt(3)+d_deadtime+d_switch","source_update":"100-us latched p_s_cmd with exact first-order inter-event map","guard":"excluded from Gate B1; software trip modeled","sensing_delay":"one previous 50-us filtered sample includes 2+8 us front-end latency","loss":"P0+1.5 R_loss ||i_f||^2","sampled_poles":"log(z)/T"},
       "validation_summary":{"all_checks_pass":result["all_checks_pass"],"failed_checks":result["failed_checks"],"worst_spectral_radius":result["hybrid_phase_sweep"]["worst"]["spectral_radius"]},
       "files":{"script":{"path":str(script_path),"sha256":sha256(script_path)},"matrices":{"path":str(matrix_path),"sha256":sha256(matrix_path)},"phase_sweep":{"path":str(csv_path),"sha256":sha256(csv_path)}}}
    path.write_text(json.dumps(jsonable(m),indent=2),encoding="utf-8")


def main()->None:
    ap=argparse.ArgumentParser(); ap.add_argument("--bundle",type=Path,default=Path("/mnt/data/queued_dvoc_gate_candidate.npz")); ap.add_argument("--outdir",type=Path,default=Path("/mnt/data")); ap.add_argument("--phase-grid-us",type=float,default=1.0); args=ap.parse_args(); args.outdir.mkdir(parents=True,exist_ok=True)
    app,_,_,_,_=build_A1(args.bundle)
    csv_path=args.outdir/"A1_hybrid_phase_sweep.csv"
    phase=phase_sweep(app,csv_path,args.phase_grid_us)
    result,arrays=validate_A1(app,phase)
    matrix_path=args.outdir/"reference_apparatus_A1_matrices.npz"; np.savez(matrix_path,**arrays)
    res_path=args.outdir/"A1_validation_results.json"; res_path.write_text(json.dumps(jsonable(result),indent=2),encoding="utf-8")
    corr_path=args.outdir/"A1_model_corrections.md"; write_corrections(corr_path,result,app)
    manifest_path=args.outdir/"reference_apparatus_A1_manifest.json"; write_manifest(manifest_path,app,result,matrix_path,csv_path,Path(__file__).resolve())
    print(json.dumps({"decision":result["decision"],"all_checks_pass":result["all_checks_pass"],"failed_checks":result["failed_checks"],"worst":result["hybrid_phase_sweep"]["worst"],"files":[str(Path(__file__).resolve()),str(matrix_path),str(manifest_path),str(res_path),str(csv_path),str(corr_path)]},indent=2))

if __name__=="__main__": main()
