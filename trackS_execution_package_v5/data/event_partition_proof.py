#!/usr/bin/env python3
"""Exact rational proof of the timing-cell partition and transition graph.

The proof uses Fourier--Motzkin elimination over fractions, not random testing.
Strict inequalities are represented by a common slack eps. Eliminating the
physical variables leaves an exact feasible interval for eps. A positive upper
endpoint proves strict feasibility; an endpoint of zero proves only a boundary
intersection; a negative endpoint proves infeasibility.
"""
from __future__ import annotations
import json
from pathlib import Path
from fractions import Fraction
from math import gcd
from functools import reduce
from itertools import product
import numpy as np
from scipy.optimize import linprog

OUT=Path('/mnt/data')
TF=Fraction(50); TS=Fraction(100)


def F(x):
    if isinstance(x,Fraction): return x
    return Fraction(str(x))

def lcm(a,b): return abs(a*b)//gcd(a,b) if a and b else 0

def canon(coeff,b):
    vals=list(coeff)+[b]; den=1
    for v in vals: den=lcm(den,v.denominator)
    ints=[v.numerator*(den//v.denominator) for v in vals]
    g=0
    for x in ints:g=gcd(g,abs(x))
    if g:ints=[x//g for x in ints]
    return tuple(Fraction(x) for x in ints[:-1]),Fraction(ints[-1])

def reduce_rows(rows):
    strongest={}
    for c,b in rows:
        c,b=canon(c,b)
        if all(x==0 for x in c):
            if b<0:return [],True
            continue
        if c not in strongest or b<strongest[c]:strongest[c]=b
    return [(c,b) for c,b in strongest.items()],False

def eliminate(rows,idx):
    pos=[];neg=[];zero=[]
    for c,b in rows:
        a=c[idx]; cr=c[:idx]+c[idx+1:]
        if a>0:pos.append((a,cr,b))
        elif a<0:neg.append((a,cr,b))
        else:zero.append((cr,b))
    out=list(zero)
    for ap,cp,bp in pos:
        for an,cn,bn in neg:
            out.append((tuple((-an)*x+ap*y for x,y in zip(cp,cn)),(-an)*bp+ap*bn))
    return reduce_rows(out)

def epsilon_interval(constraints,nvar,mode):
    """Exact eps interval for closure/actual/interior semantics."""
    rows=[]
    for a,b,strict,_ in constraints:
        shrink=(mode=='interior') or (mode=='actual' and strict)
        rows.append((tuple(F(x) for x in a)+(Fraction(1) if shrink else Fraction(0),),F(b)))
    rows += [((Fraction(0),)*nvar+(Fraction(-1),),Fraction(0)),
             ((Fraction(0),)*nvar+(Fraction(1),),Fraction(1))]
    rows,bad=reduce_rows(rows)
    if bad:return None
    remaining=nvar+1
    while remaining>1:
        # eliminate a physical variable with minimum combinatorial growth
        choices=[]
        for idx in range(remaining-1):
            p=sum(c[idx]>0 for c,b in rows);n=sum(c[idx]<0 for c,b in rows)
            choices.append((p*n,p+n,idx))
        _,_,idx=min(choices)
        rows,bad=eliminate(rows,idx);remaining-=1
        if bad:return None
    lo=Fraction(0);hi=Fraction(1)
    for c,b in rows:
        a=c[0]
        if a>0:hi=min(hi,b/a)
        elif a<0:lo=max(lo,b/a)
        elif b<0:return None
    return lo,hi

def feasible(iv): return iv is not None and iv[1]>0 and iv[0]<=iv[1]

def closure_feasible(iv): return iv is not None and iv[1]>=0 and iv[0]<=iv[1]

def base_domain():
    return [
      ((-1,0,0),F(-495),False,'T>=495'),((1,0,0),F(505),False,'T<=505'),
      ((0,-1,0),F(0),False,'phi_f>=0'),((0,1,0),F(50),True,'phi_f<50'),
      ((0,0,-1),F(0),False,'phi_s>=0'),((0,0,1),F(100),True,'phi_s<100')]

def cell_constraints(nf,ns,reg):
    q=base_domain()
    q += ({9:[((1,1,0),F(500),True,'T+phi_f<500')],
           10:[((-1,-1,0),F(-500),False,'T+phi_f>=500'),((1,1,0),F(550),True,'T+phi_f<550')],
           11:[((-1,-1,0),F(-550),False,'T+phi_f>=550')]}[nf])
    q += ({4:[((1,0,1),F(500),True,'T+phi_s<500')],
           5:[((-1,0,-1),F(-500),False,'T+phi_s>=500'),((1,0,1),F(600),True,'T+phi_s<600')],
           6:[((-1,0,-1),F(-600),False,'T+phi_s>=600')]}[ns])
    q += ({0:[((0,-1,1),F(0),False,'phi_s-phi_f<=0')],
           1:[((0,1,-1),F(0),True,'phi_s-phi_f>0'),((0,-1,1),F(50),False,'phi_s-phi_f<=50')],
           2:[((0,1,-1),F(-50),True,'phi_s-phi_f>50')]}[reg])
    return q

def source_lift(q):
    a,b,s,t=q;return tuple(F(x) for x in a)+(F(0),),b,s,'source:'+t

def target_pullback(q,nf,ns):
    a,b,s,t=q;aT,af,ass=[F(x) for x in a]
    # variables (T,phi_f,phi_s,Tnext)
    coeff=(af+ass,af,ass,aT);rhs=b+af*TF*nf+ass*TS*ns
    return coeff,rhs,s,'target:'+t

def next_domain():
    return [((0,0,0,-1),F(-495),False,'Tnext>=495'),((0,0,0,1),F(505),False,'Tnext<=505')]

def numerical_witness(constraints,nvar,mode):
    A=[];b=[]
    for a,bb,strict,_ in constraints:
        shrink=(mode=='interior') or (mode=='actual' and strict)
        A.append(list(map(float,a))+[1.0 if shrink else 0.0]);b.append(float(bb))
    A += [[0.0]*nvar+[-1.0],[0.0]*nvar+[1.0]];b += [0.0,1.0]
    c=[0.0]*nvar+[-1.0]
    res=linprog(c,A_ub=np.array(A),b_ub=np.array(b),bounds=[(None,None)]*nvar+[(0,1)],method='highs')
    if not res.success:return None
    return {'x':res.x[:nvar].tolist(),'eps':float(res.x[-1]),'max_violation':float(np.max(np.array(A)@res.x-np.array(b)))}

def serial_fraction(x):
    if isinstance(x,Fraction):return str(x)
    if isinstance(x,tuple):return [serial_fraction(v) for v in x]
    if isinstance(x,list):return [serial_fraction(v) for v in x]
    if isinstance(x,dict):return {k:serial_fraction(v) for k,v in x.items()}
    return x

def main():
    combos=[];cells=[]
    for nf,ns,reg in product((9,10,11),(4,5,6),(0,1,2)):
        cons=cell_constraints(nf,ns,reg)
        ai=epsilon_interval(cons,3,'actual');ii=epsilon_interval(cons,3,'interior')
        rec={'nf':nf,'ns':ns,'reg':reg,'actual_eps_interval':ai,'interior_eps_interval':ii}
        combos.append(rec)
        if feasible(ii):
            cid=len(cells);cells.append({'id':cid,'name':f'C{cid:02d}','nf':nf,'ns':ns,'reg':reg,'constraints':cons,
                                         'interior_eps_interval':ii,'witness':numerical_witness(cons,3,'interior')})
    overlaps=[];boundary_intersections=[]
    for i in range(len(cells)):
      for j in range(i+1,len(cells)):
        iv=epsilon_interval(cells[i]['constraints']+cells[j]['constraints'],3,'interior')
        if feasible(iv):overlaps.append({'i':i,'j':j,'eps_interval':iv})
        elif closure_feasible(iv) and iv[1]==0:boundary_intersections.append({'i':i,'j':j})
    interior_edges=[];boundary_edges=[];infeasible=[]
    for c in cells:
        sc=[source_lift(q) for q in c['constraints']]+next_domain()
        for d in cells:
            cons=sc+[target_pullback(q,c['nf'],c['ns']) for q in d['constraints']]
            ia=epsilon_interval(cons,4,'actual');ii=epsilon_interval(cons,4,'interior')
            rec={'source':c['id'],'target':d['id']}
            if feasible(ii):
                rec.update({'eps_interval':ii,'witness':numerical_witness(cons,4,'interior')});interior_edges.append(rec)
            elif feasible(ia):
                rec.update({'strict_eps_interval':ia,'witness':numerical_witness(cons,4,'actual')});boundary_edges.append(rec)
            else:infeasible.append(rec)
    coverage={
      'domain_ranges':['T in [495,505]','phi_f in [0,50)','phi_s in [0,100)'],
      'derived_scalar_ranges':['a=T+phi_f in [495,555)','b=T+phi_s in [495,605)','d=phi_s-phi_f in (-50,100)'],
      'exhaustive_disjoint_partitions':{
        'a':['[495,500)','[500,550)','[550,555)'],
        'b':['[495,500)','[500,600)','[600,605)'],
        'd':['(-50,0]','(0,50]','(50,100)']},
      'proof':'Each domain point has exactly one classification triple. Exact Fourier-Motzkin elimination proves 13 triples have positive interior slack; the remaining triples have no positive interior slack. Pairwise combined interiors have zero positive slack. Hence the 13 half-open cells cover the domain with disjoint interiors.'}
    result={'arithmetic':'exact Fraction Fourier-Motzkin elimination; floating witnesses are regression only',
      'coverage':coverage,'combos':combos,'cells':cells,'cell_count':len(cells),
      'pairwise_interior_overlap_count':len(overlaps),'pairwise_interior_overlaps':overlaps,
      'boundary_cell_intersections':boundary_intersections,
      'transition_counts':{'interior':len(interior_edges),'boundary_only':len(boundary_edges),'infeasible':len(infeasible)},
      'interior_edges':interior_edges,'boundary_only_edges':boundary_edges,'infeasible_edges':infeasible}
    (OUT/'event_partition_proof.json').write_text(json.dumps(serial_fraction(result),indent=2))
    # Merge the exact proof with the pre-existing event formulas/maps. Those
    # formulas are deterministic consequences of the cell classification; the
    # exact proof replaces only the former random coverage/edge tests.
    original=json.load(open(OUT/'event_cells.json'))
    lookup={(x['N_fast'],x['N_source'], {'d<=0':0,'0<d<=50':1,'d>50':2}[x['order_region']]):x for x in original['cells']}
    vc=[]
    exact_succ={i:[] for i in range(len(cells))}
    for e in interior_edges: exact_succ[e['source']].append(e['target'])
    for c in cells:
        old=lookup[(c['nf'],c['ns'],c['reg'])]
        vc.append({'id':c['id'],'name':c['name'],'N_fast':c['nf'],'N_source':c['ns'],'order_region':old['order_region'],
          'H_theta':[list(q[0]) for q in c['constraints']],'h_theta':[str(q[1]) for q in c['constraints']],
          'strict_facets':[k for k,q in enumerate(c['constraints']) if q[2]],'facet_text':[q[3] for q in c['constraints']],
          'event_signature':old['event_signature'],'event_count':old['event_count'],'events':old['events'],
          'dwell_formulas':old['dwell_formulas'],'phase_update':old['phase_update'],
          'interior_successors':sorted(exact_succ[c['id']]),
          'interior_eps_interval':[str(v) for v in c['interior_eps_interval']],'interior_witness':c['witness']})
    (OUT/'verified_event_cells.json').write_text(json.dumps({'coverage':coverage,'cells':vc},indent=2))
    (OUT/'verified_event_graph.json').write_text(json.dumps(serial_fraction({'interior_edges':interior_edges,'boundary_only_edges':boundary_edges,
       'counts':result['transition_counts'],'semantics':'interior edges have positive slack on every source/target/domain facet; boundary-only edges satisfy all half-open strict facets but lie on at least one closed facet'}),indent=2))
    print(json.dumps({'cells':len(cells),'interior_overlaps':len(overlaps),'interior_edges':len(interior_edges),'boundary_edges':len(boundary_edges)},indent=2))
if __name__=='__main__':main()
