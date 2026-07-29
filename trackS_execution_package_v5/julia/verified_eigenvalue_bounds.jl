using LinearAlgebra
using NPZ
using JSON3
using IntervalArithmetic
using IntervalLinearAlgebra

root = length(ARGS) >= 1 ? abspath(ARGS[1]) : abspath(joinpath(@__DIR__, ".."))
kind = length(ARGS) >= 2 ? lowercase(ARGS[2]) : error("kind common|graph required")
candidate_path = length(ARGS) >= 3 ? abspath(ARGS[3]) : error("candidate path required")
enclosure_path = length(ARGS) >= 4 ? abspath(ARGS[4]) : joinpath(root, "results", "interval_timing_enclosures.npz")
model_scope = "frozen rounded canonical A1 linearized hybrid model and declared timing continuum; physical component tolerances are not included"

function thin_matrix(A)
    return [interval(Float64(A[i,j])) for i in axes(A,1), j in axes(A,2)]
end

function symmetric_interval(A)
    return (A + A') / interval(2.0)
end

function verified_min_eigen(A)
    evals, _evecs, cert = verify_eigen(A)
    all(cert) || return (-Inf, false, Any[])
    lower = minimum(inf(real(value)) for value in evals)
    return lower, true, evals
end

function norm_upper(A)
    mag = [max(abs(inf(A[i,j])), abs(sup(A[i,j]))) for i in axes(A,1), j in axes(A,2)]
    row = [sum(interval(mag[i,j]) for j in axes(mag,2)) for i in axes(mag,1)]
    col = [sum(interval(mag[i,j]) for i in axes(mag,1)) for j in axes(mag,2)]
    return sup(sqrt(interval(maximum(sup(value) for value in row)) * interval(maximum(sup(value) for value in col))))
end

function candidate_tau(candidate)
    tau_cert = haskey(candidate, "tau_cert") ? Float64(candidate["tau_cert"]) : (haskey(candidate, "tau") ? Float64(candidate["tau"]) : 0.0)
    tau_solver = haskey(candidate, "tau_solver") ? Float64(candidate["tau_solver"]) : NaN
    return tau_cert, tau_solver
end

candidate = npzread(candidate_path)
enclosure = npzread(enclosure_path)
required = [
    "F_cell_center", "F_cell_rad", "delta_cell_2_bound",
    "F_edge_center", "F_edge_rad", "delta_edge_2_bound", "edges",
    "cell_center_contained", "edge_center_contained", "edge_exact_domain_used",
    "all_authoritative_centers_contained",
]
missing = [key for key in required if !haskey(enclosure, key)]
if !isempty(missing)
    error("enclosure missing fail-closed fields: $(missing)")
end
cell_contained = Bool.(enclosure["cell_center_contained"])
edge_contained = Bool.(enclosure["edge_center_contained"])
edge_exact = Bool.(enclosure["edge_exact_domain_used"])
all_contained = Bool(enclosure["all_authoritative_centers_contained"][1]) && all(cell_contained) && all(edge_contained)
delta_cell = Float64.(enclosure["delta_cell_2_bound"])
delta_edge = Float64.(enclosure["delta_edge_2_bound"])
all_valid = all_contained && all(edge_exact) && all(isfinite, delta_cell) && all(isfinite, delta_edge) && all(delta_cell .>= 0.0) && all(delta_edge .>= 0.0)
all_valid || error("fail-closed enclosure checks failed")

gamma = Float64(candidate["gamma"])
tau_cert, tau_solver = candidate_tau(candidate)
gamma_interval = interval(gamma)
records = Any[]
all_positive = true
all_contraction = gamma < 1.0
all_tau_preserved = true

if kind == "common"
    P = Float64.(candidate["P"])
    PI = symmetric_interval(thin_matrix(P))
    pmin, p_verified, _ = verified_min_eigen(PI)
    all_positive &= p_verified && pmin > 0
    centers = Float64.(enclosure["F_cell_center"])
    for cell in axes(centers, 1)
        CI = thin_matrix(centers[cell,:,:])
        nominal_matrix = symmetric_interval(gamma_interval^2 .* PI - CI' * PI * CI)
        nominal, eigen_verified, _ = verified_min_eigen(nominal_matrix)
        delta = sup(interval(delta_cell[cell]))
        penalty = sup(interval(2.0) * interval(norm_upper(PI * CI)) * interval(delta) + interval(norm_upper(PI)) * interval(delta)^2)
        robust_lower = prevfloat(nominal - penalty)
        robust_after_tau = prevfloat(robust_lower - tau_cert)
        contraction_ok = eigen_verified && robust_lower > 0 && gamma < 1.0
        tau_preserved = eigen_verified && robust_after_tau > 0
        all_contraction &= contraction_ok
        all_tau_preserved &= tau_preserved
        push!(records, Dict(
            "cell"=>cell-1,
            "nominal_min_eig_lower"=>nominal,
            "tau_cert_double"=>tau_cert,
            "tau_solver"=>tau_solver,
            "delta_2_upper"=>delta,
            "penalty_upper"=>penalty,
            "robust_contraction_lower_bound"=>robust_lower,
            "robust_margin_after_tau"=>robust_after_tau,
            "verified_eigen"=>eigen_verified,
            "contraction_certified"=>contraction_ok,
            "center_tau_preserved"=>tau_preserved,
        ))
    end
    global_robust = minimum(record["robust_contraction_lower_bound"] for record in records)
    global_after_tau = minimum(record["robust_margin_after_tau"] for record in records)
    certificate = all_positive && all_contraction
    result = Dict(
        "kind"=>"common",
        "model_scope"=>model_scope,
        "verification_level"=>"verified_interval_eigenvalue_and_outward_norm_bound",
        "gamma"=>gamma,
        "gamma_is_contractive"=>gamma < 1.0,
        "tau_cert_double"=>tau_cert,
        "tau_solver"=>tau_solver,
        "min_eig_P_lower"=>pmin,
        "P_positive_verified"=>p_verified,
        "enclosure_fail_closed_checks_passed"=>all_valid,
        "records"=>records,
        "robust_contraction_lower_bound"=>global_robust,
        "robust_margin_after_tau"=>global_after_tau,
        "contraction_certified"=>certificate,
        "center_tau_preserved"=>all_tau_preserved,
        "certificate_verified"=>certificate,
    )
elseif kind == "graph"
    Ps = [Float64.(candidate["P_$(lpad(index,2,'0'))"]) for index in 0:12]
    PIs = [symmetric_interval(thin_matrix(P)) for P in Ps]
    pinfo = [verified_min_eigen(P) for P in PIs]
    all_positive &= all(info[2] && info[1] > 0 for info in pinfo)
    centers = Float64.(enclosure["F_edge_center"])
    edges = Int.(enclosure["edges"])
    for edge_index in axes(edges, 1)
        source, target = edges[edge_index,1] + 1, edges[edge_index,2] + 1
        CI = thin_matrix(centers[edge_index,:,:])
        nominal_matrix = symmetric_interval(gamma_interval^2 .* PIs[source] - CI' * PIs[target] * CI)
        nominal, eigen_verified, _ = verified_min_eigen(nominal_matrix)
        delta = sup(interval(delta_edge[edge_index]))
        penalty = sup(interval(2.0) * interval(norm_upper(PIs[target] * CI)) * interval(delta) + interval(norm_upper(PIs[target])) * interval(delta)^2)
        robust_lower = prevfloat(nominal - penalty)
        robust_after_tau = prevfloat(robust_lower - tau_cert)
        contraction_ok = eigen_verified && robust_lower > 0 && gamma < 1.0
        tau_preserved = eigen_verified && robust_after_tau > 0
        all_contraction &= contraction_ok
        all_tau_preserved &= tau_preserved
        push!(records, Dict(
            "edge_index"=>edge_index-1,
            "source"=>source-1,
            "target"=>target-1,
            "nominal_min_eig_lower"=>nominal,
            "tau_cert_double"=>tau_cert,
            "tau_solver"=>tau_solver,
            "delta_2_upper"=>delta,
            "penalty_upper"=>penalty,
            "robust_contraction_lower_bound"=>robust_lower,
            "robust_margin_after_tau"=>robust_after_tau,
            "verified_eigen"=>eigen_verified,
            "contraction_certified"=>contraction_ok,
            "center_tau_preserved"=>tau_preserved,
        ))
    end
    global_robust = minimum(record["robust_contraction_lower_bound"] for record in records)
    global_after_tau = minimum(record["robust_margin_after_tau"] for record in records)
    certificate = all_positive && all_contraction
    result = Dict(
        "kind"=>"graph",
        "model_scope"=>model_scope,
        "verification_level"=>"verified_interval_eigenvalue_and_outward_norm_bound",
        "gamma"=>gamma,
        "gamma_is_contractive"=>gamma < 1.0,
        "tau_cert_double"=>tau_cert,
        "tau_solver"=>tau_solver,
        "min_eig_P_lower"=>minimum(info[1] for info in pinfo),
        "all_P_positive_verified"=>all(info[2] for info in pinfo),
        "enclosure_fail_closed_checks_passed"=>all_valid,
        "records"=>records,
        "robust_contraction_lower_bound"=>global_robust,
        "robust_margin_after_tau"=>global_after_tau,
        "contraction_certified"=>certificate,
        "center_tau_preserved"=>all_tau_preserved,
        "certificate_verified"=>certificate,
    )
else
    error("kind must be common or graph")
end

outfile = joinpath(root, "results", "verified_eigenvalue_bounds_$(kind).json")
open(outfile, "w") do io
    JSON3.pretty(io, result)
end
println(JSON3.write(Dict(
    "kind"=>kind,
    "certificate_verified"=>result["certificate_verified"],
    "robust_contraction_lower_bound"=>result["robust_contraction_lower_bound"],
    "robust_margin_after_tau"=>result["robust_margin_after_tau"],
)))
exit(result["certificate_verified"] ? 0 : 3)
