using LinearAlgebra
using JuMP
import MathOptInterface as MOI
using NPZ
using JSON3

root = length(ARGS) >= 1 ? abspath(ARGS[1]) : abspath(joinpath(@__DIR__, ".."))
solver_name = length(ARGS) >= 2 ? uppercase(ARGS[2]) : "CLARABEL"

function optimizer_for(name)
    if name == "MOSEK"
        @eval using MosekTools
        return MosekTools.Optimizer, false
    elseif name == "CLARABEL"
        @eval using Clarabel
        return Clarabel.Optimizer, false
    elseif name == "SCS"
        @eval using SCS
        return SCS.Optimizer, true
    else
        error("Unsupported Julia solver $name; use MOSEK or CLARABEL for a primary run, SCS for screening")
    end
end

function solve_margin(Fedge, edges, gamma, solver_name; eps_pd=1e-11)
    cell_count, n = 13, size(Fedge, 2)
    optimizer, screening = optimizer_for(solver_name)
    model = Model(optimizer)
    set_silent(model)
    if solver_name == "SCS"
        set_optimizer_attribute(model, "eps_abs", 1e-8)
        set_optimizer_attribute(model, "eps_rel", 1e-8)
        set_optimizer_attribute(model, "max_iters", 1_000_000)
    elseif solver_name == "CLARABEL"
        set_optimizer_attribute(model, "max_iter", 500)
        set_optimizer_attribute(model, "tol_gap_abs", 1e-10)
        set_optimizer_attribute(model, "tol_gap_rel", 1e-10)
        set_optimizer_attribute(model, "tol_feas", 1e-10)
        set_optimizer_attribute(model, "tol_infeas_abs", 1e-10)
        set_optimizer_attribute(model, "tol_infeas_rel", 1e-10)
    end
    Ps = [@variable(model, [1:n,1:n], PSD, base_name="P_$(cell-1)") for cell in 1:cell_count]
    @variable(model, tau)
    @constraint(model, sum(Ps[cell][i,i] for cell in 1:cell_count for i in 1:n) == 1.0)
    for cell in 1:cell_count
        @constraint(model, Symmetric(Ps[cell] - eps_pd * Matrix{Float64}(I,n,n)) in PSDCone())
    end
    for edge_index in axes(edges, 1)
        source, target = Int(edges[edge_index,1]) + 1, Int(edges[edge_index,2]) + 1
        Fe = Matrix(Fedge[edge_index,:,:])
        @constraint(model, Symmetric(gamma^2 .* Ps[source] .- Fe' * Ps[target] * Fe .- tau .* Matrix{Float64}(I,n,n)) in PSDCone())
    end
    @objective(model, Max, tau)
    optimize!(model)
    termination, primal = termination_status(model), primal_status(model)
    status_ok = termination == MOI.OPTIMAL && primal == MOI.FEASIBLE_POINT
    return status_ok, screening, status_ok ? [value.(P) for P in Ps] : nothing, status_ok ? value(tau) : nothing, string(termination), string(primal), model
end

function verify(Ps, Fedge, edges, gamma, tau_solver)
    Ps = [(P + P') / 2 for P in Ps]
    pmins = [eigmin(Symmetric(P)) for P in Ps]
    pmaxs = [eigmax(Symmetric(P)) for P in Ps]
    raw = Float64[]
    shifted_solver = Float64[]
    for edge_index in axes(edges, 1)
        source, target = Int(edges[edge_index,1]) + 1, Int(edges[edge_index,2]) + 1
        Fe = Matrix(Fedge[edge_index,:,:])
        base = gamma^2 * Ps[source] - Fe' * Ps[target] * Fe
        margin = eigmin(Symmetric((base + base') / 2))
        push!(raw, margin)
        push!(shifted_solver, margin - tau_solver)
    end
    return minimum(pmins), maximum(pmaxs), maximum(pmaxs ./ pmins), minimum(raw), minimum(shifted_solver), raw, shifted_solver
end

z = npzread(joinpath(root, "data", "authoritative_center_maps.npz"))
Fedge = Float64.(z["F_edge_centers"])
edges = Int.(z["edges"])
lo, hi = 0.0, 1.05
eps_pd, tau_accept = 1e-11, 1e-10
best_metric = nothing
best_contraction = nothing
history = Any[]
for iteration in 1:45
    gamma = (lo + hi) / 2
    ok, screening, Ps, tau_solver, termination, primal, _model = solve_margin(Fedge, edges, gamma, solver_name; eps_pd=eps_pd)
    record = Dict("iteration"=>iteration, "gamma"=>gamma, "termination"=>termination, "primal_status"=>primal, "screening_solver"=>screening)
    metric_feasible = false
    contraction_candidate = false
    if ok
        pmin, pmax, condmax, tau_cert, shifted_min, raw, shifted = verify(Ps, Fedge, edges, gamma, tau_solver)
        metric_feasible = !screening && pmin > eps_pd && tau_cert > tau_accept
        contraction_candidate = metric_feasible && gamma < 1.0
        record["tau_solver"] = tau_solver
        record["tau_cert_double"] = tau_cert
        record["min_eig_P"] = pmin
        record["max_eig_P"] = pmax
        record["max_condition_P"] = condmax
        record["min_raw_lmi_slack"] = tau_cert
        record["min_shifted_by_solver_tau_slack"] = shifted_min
        record["center_metric_feasible"] = metric_feasible
        record["center_contraction_candidate"] = contraction_candidate
        if metric_feasible
            hi = gamma
            best_metric = (gamma, Ps, tau_solver, tau_cert, pmin, pmax, condmax, raw, shifted, termination, primal)
            if contraction_candidate
                best_contraction = best_metric
            end
        else
            lo = gamma
        end
    else
        record["center_metric_feasible"] = false
        record["center_contraction_candidate"] = false
        lo = gamma
    end
    push!(history, record)
end

mkpath(joinpath(root, "results"))
output = Dict(
    "kind"=>"edge_conditioned_graph_margin_sdp_jump",
    "model_scope"=>"frozen rounded canonical A1 linearized hybrid model and exact edge-conditioned timing representatives",
    "authoritative_maps"=>"45 regenerated edge-conditioned canonical A1 maps",
    "normalization"=>"sum_c trace(P_c)=1",
    "solver"=>solver_name,
    "screening_only"=>(solver_name == "SCS"),
    "edge_count"=>45,
    "epsilon_P"=>eps_pd,
    "tau_accept"=>tau_accept,
    "gamma_lower"=>lo,
    "gamma_upper"=>hi,
    "history"=>history,
)
selected = best_contraction === nothing ? best_metric : best_contraction
if selected === nothing
    output["status"] = solver_name == "SCS" ? "SCREENING_RUN_ONLY_NO_CERTIFICATE" : "NO_ACCEPTED_PRIMARY_SOLVER_CENTER_METRIC_FROM_THIS_RUN"
else
    gamma, Ps, tau_solver, tau_cert, pmin, pmax, condmax, raw, shifted, termination, primal = selected
    is_contraction = gamma < 1.0
    output["status"] = is_contraction ? "CENTER_CONTRACTION_CANDIDATE_PENDING_PROOF_GRADE_VERIFICATION" : "CENTER_METRIC_ONLY_GAMMA_NOT_BELOW_ONE"
    output["gamma"] = gamma
    output["gamma_is_contractive"] = is_contraction
    output["tau_solver"] = tau_solver
    output["tau_cert_double"] = tau_cert
    output["min_eig_P"] = pmin
    output["max_eig_P"] = pmax
    output["max_condition_P"] = condmax
    output["termination"] = termination
    output["primal_status"] = primal
    payload = Dict{String,Any}("gamma"=>gamma, "tau"=>tau_cert, "tau_cert"=>tau_cert, "tau_solver"=>tau_solver, "edges"=>edges)
    for cell in 1:length(Ps)
        payload["P_$(lpad(cell-1,2,'0'))"] = Ps[cell]
    end
    npzwrite(joinpath(root, "results", "graph_center_certificates_jump_$(lowercase(solver_name)).npz"), payload)
end
open(joinpath(root, "results", "graph_solver_results_jump_$(lowercase(solver_name)).json"), "w") do io
    JSON3.pretty(io, output)
end
println(JSON3.write(Dict(key=>value for (key,value) in output if key != "history")))
exit(best_contraction === nothing ? 3 : 0)
