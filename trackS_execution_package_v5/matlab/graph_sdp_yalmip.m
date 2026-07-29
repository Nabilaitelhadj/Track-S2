function result = graph_sdp_yalmip(root, solver)
% Edge-conditioned graph-dependent margin-maximization SDP.
if nargin < 1, root = fileparts(fileparts(mfilename('fullpath'))); end
if nargin < 2, solver = 'mosek'; end
D=load(fullfile(root,'data','authoritative_trackS_problem.mat'));
F=D.F_edge_centers; edges=D.edges_one_based; [n,~,E]=size(F); C=13;
eps_pd=1e-11;tau_accept=1e-10;screening=strcmpi(solver,'scs');
ops=sdpsettings('solver',solver,'verbose',0,'savesolverinput',1,'savesolveroutput',1);
lo=0.0;hi=1.05;best=[];hist=struct([]);
for it=1:45
    gamma=(lo+hi)/2;P=cell(C,1);Con=[];trace_sum=0;tau=sdpvar(1,1);
    for c=1:C
        P{c}=sdpvar(n,n,'symmetric');Con=[Con,P{c}>=eps_pd*eye(n)];trace_sum=trace_sum+trace(P{c}); %#ok<AGROW>
    end
    % One global normalization preserves relative scaling among Lyapunov pieces.
    Con=[Con,trace_sum==1];
    for e=1:E
        c=edges(e,1);d=edges(e,2);
        Con=[Con,gamma^2*P{c}-F(:,:,e)'*P{d}*F(:,:,e)>=tau*eye(n)]; %#ok<AGROW>
    end
    sol=optimize(Con,-tau,ops);rec.iteration=it;rec.gamma=gamma;rec.problem=sol.problem;rec.info=sol.info;rec.screening_solver=screening;
    accepted=false;
    if sol.problem==0
        Pv=cellfun(@value,P,'UniformOutput',false);tauv=value(tau);pmins=zeros(C,1);pmaxs=zeros(C,1);raw=zeros(E,1);shift=zeros(E,1);
        for c=1:C,ev=eig((Pv{c}+Pv{c}')/2);pmins(c)=min(ev);pmaxs(c)=max(ev);end
        for e=1:E,c=edges(e,1);d=edges(e,2);M=gamma^2*Pv{c}-F(:,:,e)'*Pv{d}*F(:,:,e);M=(M+M')/2;raw(e)=min(eig(M));shift(e)=raw(e)-tauv;end
        rec.tau=tauv;rec.min_eig_P=min(pmins);rec.max_eig_P=max(pmaxs);rec.max_condition_P=max(pmaxs./pmins);
        rec.min_raw_lmi_slack=min(raw);rec.min_shifted_lmi_slack=min(shift);
        tau_cert=min(raw);
        metric_feasible=~screening && min(pmins)>eps_pd && tau_cert>tau_accept;
        accepted=metric_feasible && gamma<1.0;
        rec.tau_solver=tauv;rec.tau_cert_double=tau_cert;rec.center_metric_feasible=metric_feasible;rec.center_contraction_candidate=accepted;
        rec.accepted_for_metric_bisection=metric_feasible;
        if metric_feasible
            hi=gamma;best.gamma=gamma;best.tau_solver=tauv;best.tau_cert=tau_cert;best.P=Pv;best.raw_slacks=raw;best.shifted_slacks=shift;best.is_contraction=(gamma<1.0);
            best.min_eig_P=min(pmins);best.max_eig_P=max(pmaxs);best.max_condition_P=max(pmaxs./pmins);best.problem=sol.problem;best.info=sol.info;
        else,lo=gamma;end
    else,rec.accepted_for_certificate_bisection=false;lo=gamma;end
    hist(it)=rec; %#ok<AGROW>
end
result.kind='edge_conditioned_graph_margin_sdp_yalmip';result.authoritative_maps='45 regenerated edge-conditioned canonical A1 maps';
result.normalization='sum_c trace(P_c)=1';result.solver=solver;result.screening_only=screening;result.edge_count=E;
result.epsilon_P=eps_pd;result.tau_accept=tau_accept;result.gamma_lower=lo;result.gamma_upper=hi;result.history=hist;
if isempty(best)
    if screening,result.status='SCREENING_RUN_ONLY_NO_CERTIFICATE';else,result.status='NO_ACCEPTED_PRIMARY_SOLVER_CANDIDATE_FROM_THIS_RUN';end
else
    if best.is_contraction,result.status='CENTER_CONTRACTION_CANDIDATE_PENDING_PROOF_GRADE_VERIFICATION';else,result.status='CENTER_METRIC_ONLY_GAMMA_NOT_BELOW_ONE';end;result.gamma=best.gamma;result.gamma_is_contractive=best.is_contraction;result.tau_solver=best.tau_solver;result.tau_cert_double=best.tau_cert;
    result.min_eig_P=best.min_eig_P;result.max_eig_P=best.max_eig_P;result.max_condition_P=best.max_condition_P;
    result.min_raw_lmi_slack=min(best.raw_slacks);result.min_shifted_lmi_slack=min(best.shifted_slacks);
    save(fullfile(root,'results',['graph_center_certificates_yalmip_' lower(solver) '.mat']),'-struct','best');
end
fid=fopen(fullfile(root,'results',['graph_solver_results_yalmip_' lower(solver) '.json']),'w');fwrite(fid,jsonencode(result),'char');fclose(fid);disp(result)
end
