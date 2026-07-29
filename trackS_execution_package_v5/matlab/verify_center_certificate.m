function result = verify_center_certificate(root, kind, candidate_file)
% Independent double-precision diagnostic verification of a MATLAB candidate.
% Final proof-grade verification remains the Julia interval/eigenvalue stage.
if nargin < 1, root=fileparts(fileparts(mfilename('fullpath'))); end
D=load(fullfile(root,'data','authoritative_trackS_problem.mat'));Z=load(candidate_file);gamma=Z.gamma;
if isfield(Z,'tau_cert'),tau_cert=Z.tau_cert;elseif isfield(Z,'tau'),tau_cert=Z.tau;else,tau_cert=0;end
if isfield(Z,'tau_solver'),tau_solver=Z.tau_solver;else,tau_solver=NaN;end
if strcmpi(kind,'common')
 F=D.F_cell_centers;C=size(F,3);P=Z.P;ep=eig((P+P')/2);raw=zeros(C,1);
 for c=1:C,M=gamma^2*P-F(:,:,c)'*P*F(:,:,c);M=(M+M')/2;raw(c)=min(eig(M));end
 result=struct('kind','common','verification_level','double_precision_diagnostic_only','model_scope','frozen rounded canonical A1 linearized hybrid model','gamma',gamma,'gamma_is_contractive',gamma<1,'tau_solver',tau_solver,'tau_cert_double',tau_cert,'min_eig_P',min(ep),'max_eig_P',max(ep),'min_raw_lmi_slack',min(raw),'raw_slacks',raw,'center_contraction_candidate',gamma<1 && min(ep)>0 && min(raw)>0);
else
 F=D.F_edge_centers;edges=D.edges_one_based;C=13;E=size(F,3);P=Z.P;pmins=zeros(C,1);pmaxs=zeros(C,1);raw=zeros(E,1);
 for c=1:C,ep=eig((P{c}+P{c}')/2);pmins(c)=min(ep);pmaxs(c)=max(ep);end
 for e=1:E,c=edges(e,1);d=edges(e,2);M=gamma^2*P{c}-F(:,:,e)'*P{d}*F(:,:,e);M=(M+M')/2;raw(e)=min(eig(M));end
 result=struct('kind','graph','verification_level','double_precision_diagnostic_only','model_scope','frozen rounded canonical A1 linearized hybrid model','gamma',gamma,'gamma_is_contractive',gamma<1,'tau_solver',tau_solver,'tau_cert_double',tau_cert,'min_eig_P',min(pmins),'max_eig_P',max(pmaxs),'min_raw_lmi_slack',min(raw),'raw_slacks',raw,'center_contraction_candidate',gamma<1 && min(pmins)>0 && min(raw)>0);
end
disp(result)
end
