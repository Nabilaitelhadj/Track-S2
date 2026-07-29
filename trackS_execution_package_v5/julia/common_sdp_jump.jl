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

function solve_margin(F, gamma, solver_name; eps_pd=1e-10)
    n = size(F, 2)
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
    @variable(model, P[1:n, 1:n], PSD)
    @variable(model, tau)
    @constraint(model, sum(P[i, i] for i in 1:n) == 1.0)
    @constraint(model, Symmetric(P - eps_pd * Matrix{Float64}(I, n, n)) in PSDCone())
    for cell in axes(F, 1)
        Fc = Matrix(F[cell, :, :])
        @constraint(model, Symmetric(gamma^2 .* P .- Fc' * P * Fc .- tau .* Matrix{Float64}(I, n, n)) in PSDCone())
    end
    @objective(model, Max, tau)
    optimize!(model)
    termination = termination_status(model)
    primal = primal_status(model)
    status_ok = termination == MOI.OPTIMAL && primal == MOI.FEASIBLE_POINT
    return status_ok, screening, status_ok ? value.(P) : nothing, status_ok ? value(tau) : nothing, string(termination), string(primal), model
end

function verify(P, F, gamma, tau_solver)
    P = (P + P') / 2
    eigP = eigvals(Symmetric(P))
    raw = Float64[]
    shifted_solver = Float64[]
    for cell in axes(F, 1)
        Fc = Matrix(F[cell, :, :])
        base = gamma^2 * P - Fc' * P * Fc
        margin = eigmin(Symmetric((base + base') / 2))
        push!(raw, margin)
        push!(shifted_solver, margin - tau_solver)
    end
    return minimum(eigP), maximum(eigP), minimum(raw), minimum(shifted_solver), raw, shifted_solver
end

z = npzread(joinpath(root, "data", "authoritative_center_maps.npz"))
F = Float64.(z["F_cell_centers"])
rho = maximum(maximum(abs.(eigvals(Matrix(F[cell, :, :])))) for cell in axes(F, 1))
lo, hi = rho, max(1.05, rho + 1e-4)
eps_pd, tau_accept = 1e-10, 1e-9
best_metric = nothing
best_contraction = nothing
history = Any[]
for iteration in 1:45
    gamma = (lo + hi) / 2
    ok, screening, P, tau_solver, termination, primal, _model = solve_margin(F, gamma, solver_name; eps_pd=eps_pd)
    record = Dict("iteration"=>iteration, "gamma"=>gamma, "termination"=>termination, "primal_status"=>primal, "screening_solver"=>screening)
    metric_feasible = false
    contraction_candidate = false
    if ok
        pmin, pmax, tau_cert, shifted_min, raw, shifted = verify(P, F, gamma, tau_solver)
        metric_feasible = !screening && pmin > eps_pd && tau_cert > tau_accept
        contraction_candidate = metric_feasible && gamma < 1.0
        record["tau_solver"] = tau_solver
        record["tau_cert_double"] = tau_cert
        record["min_eig_P"] = pmin
        record["max_eig_P"] = pmax
        record["condition_P"] = pmax / pmin
        record["min_raw_lmi_slack"] = tau_cert
        record["min_shifted_by_solver_tau_slack"] = shifted_min
        record["center_metric_feasible"] = metric_feasible
        record["center_contraction_candidate"] = contraction_candidate
        if metric_feasible
            hi = gamma
            best_metric = (gamma, P, tau_solver, tau_cert, pmin, pmax, raw, shifted, termination, primal)
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
    "kind"=>"common_center_margin_sdp_jump",
    "model_scope"=>"frozen rounded canonical A1 linearized hybrid model and declared timing representatives",
    "authoritative_maps"=>"regenerated canonical A1 cell maps",
    "solver"=>solver_name,
    "screening_only"=>(solver_name == "SCS"),
    "epsilon_P"=>eps_pd,
    "tau_accept"=>tau_accept,
    "center_spectral_radius_lower_bound"=>rho,
    "gamma_lower"=>lo,
    "gamma_upper"=>hi,
    "history"=>history,
)
selected = best_contraction === nothing ? best_metric : best_contraction
if selected === nothing
    output["status"] = solver_name == "SCS" ? "SCREENING_RUN_ONLY_NO_CERTIFICATE" : "NO_ACCEPTED_PRIMARY_SOLVER_CENTER_METRIC_FROM_THIS_RUN"
else
    gamma, P, tau_solver, tau_cert, pmin, pmax, raw, shifted, termination, primal = selected
    is_contraction = gamma < 1.0
    output["status"] = is_contraction ? "CENTER_CONTRACTION_CANDIDATE_PENDING_PROOF_GRADE_VERIFICATION" : "CENTER_METRIC_ONLY_GAMMA_NOT_BELOW_ONE"
    output["gamma"] = gamma
    output["gamma_is_contractive"] = is_contraction
    output["tau_solver"] = tau_solver
    output["tau_cert_double"] = tau_cert
    output["min_eig_P"] = pmin
    output["max_eig_P"] = pmax
    output["condition_P"] = pmax / pmin
    output["termination"] = termination
    output["primal_status"] = primal
    npzwrite(joinpath(root, "results", "common_center_certificate_jump_$(lowercase(solver_name)).npz"), Dict(
        "P"=>P, "gamma"=>gamma, "tau"=>tau_cert, "tau_cert"=>tau_cert, "tau_solver"=>tau_solver
    ))
end
open(joinpath(root, "results", "common_solver_results_jump_$(lowercase(solver_name)).json"), "w") do io
    JSON3.pretty(io, output)
end
println(JSON3.write(Dict(key=>value for (key,value) in output if key != "history")))
exit(best_contraction === nothing ? 3 : 0)
