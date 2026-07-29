function result = common_sdp_yalmip(root, solver)
% Common-quadratic margin-maximization SDP on authoritative regenerated maps.
% Usage: common_sdp_yalmip('path/to/package','mosek')
if nargin < 1, root = fileparts(fileparts(mfilename('fullpath'))); end
if nargin < 2, solver = 'mosek'; end
D = load(fullfile(root,'data','authoritative_trackS_problem.mat'));
F = D.F_cell_centers; [n,~,C] = size(F);
eps_pd = 1e-10; tau_accept = 1e-9;
screening = strcmpi(solver,'scs');
ops = sdpsettings('solver',solver,'verbose',0,'savesolverinput',1,'savesolveroutput',1);
rho = 0;
for c=1:C, rho=max(rho,max(abs(eig(F(:,:,c))))); end
lo=rho; hi=max(1.05,lo+1e-4); best=[]; hist=struct([]);
for it=1:45
    gamma=(lo+hi)/2;
    P=sdpvar(n,n,'symmetric'); tau=sdpvar(1,1);
    Con=[P>=eps_pd*eye(n),trace(P)==1];
    for c=1:C
        Con=[Con,gamma^2*P-F(:,:,c)'*P*F(:,:,c)>=tau*eye(n)]; %#ok<AGROW>
    end
    sol=optimize(Con,-tau,ops);
    rec.iteration=it;rec.gamma=gamma;rec.problem=sol.problem;rec.info=sol.info;rec.screening_solver=screening;
    accepted=false;
    if sol.problem==0
        Pv=value(P);tauv=value(tau);pEig=eig((Pv+Pv')/2);raw=zeros(C,1);shift=zeros(C,1);
        for c=1:C
            M=(gamma^2*Pv-F(:,:,c)'*Pv*F(:,:,c));M=(M+M')/2;
            raw(c)=min(eig(M));shift(c)=raw(c)-tauv;
        end
        rec.tau=tauv;rec.min_eig_P=min(pEig);rec.max_eig_P=max(pEig);rec.condition_P=max(pEig)/min(pEig);
        rec.min_raw_lmi_slack=min(raw);rec.min_shifted_lmi_slack=min(shift);
        tau_cert=min(raw);
        metric_feasible=~screening && min(pEig)>eps_pd && tau_cert>tau_accept;
        accepted=metric_feasible && gamma<1.0;
        rec.tau_solver=tauv;rec.tau_cert_double=tau_cert;rec.center_metric_feasible=metric_feasible;rec.center_contraction_candidate=accepted;
        rec.accepted_for_metric_bisection=metric_feasible;
        if metric_feasible
            hi=gamma;best.gamma=gamma;best.tau_solver=tauv;best.tau_cert=tau_cert;best.P=Pv;best.raw_slacks=raw;best.shifted_slacks=shift;best.is_contraction=(gamma<1.0);
            best.min_eig_P=min(pEig);best.max_eig_P=max(pEig);best.condition_P=max(pEig)/min(pEig);best.problem=sol.problem;best.info=sol.info;
        else
            lo=gamma;
        end
    else
        rec.accepted_for_certificate_bisection=false;lo=gamma;
    end
    hist(it)=rec; %#ok<AGROW>
end
result.kind='common_center_margin_sdp_yalmip';result.authoritative_maps='regenerated canonical A1 cell maps';result.solver=solver;
result.screening_only=screening;result.epsilon_P=eps_pd;result.tau_accept=tau_accept;result.center_spectral_radius_lower_bound=rho;
result.gamma_lower=lo;result.gamma_upper=hi;result.history=hist;
if isempty(best)
    if screening,result.status='SCREENING_RUN_ONLY_NO_CERTIFICATE';else,result.status='NO_ACCEPTED_PRIMARY_SOLVER_CANDIDATE_FROM_THIS_RUN';end
else
    if best.is_contraction,result.status='CENTER_CONTRACTION_CANDIDATE_PENDING_PROOF_GRADE_VERIFICATION';else,result.status='CENTER_METRIC_ONLY_GAMMA_NOT_BELOW_ONE';end;result.gamma=best.gamma;result.gamma_is_contractive=best.is_contraction;result.tau_solver=best.tau_solver;result.tau_cert_double=best.tau_cert;
    result.min_eig_P=best.min_eig_P;result.max_eig_P=best.max_eig_P;result.condition_P=best.condition_P;
    result.min_raw_lmi_slack=min(best.raw_slacks);result.min_shifted_lmi_slack=min(best.shifted_slacks);
    save(fullfile(root,'results',['common_center_certificate_yalmip_' lower(solver) '.mat']),'-struct','best');
end
fid=fopen(fullfile(root,'results',['common_solver_results_yalmip_' lower(solver) '.json']),'w');fwrite(fid,jsonencode(result),'char');fclose(fid);disp(result)
end
