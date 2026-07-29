using LinearAlgebra
using NPZ
using JSON3
using IntervalArithmetic
using IntervalMatrices

root = length(ARGS) >= 1 ? abspath(ARGS[1]) : abspath(joinpath(@__DIR__, ".."))
data = joinpath(root, "data")
results = joinpath(root, "results")
mkpath(results)

a1 = npzread(joinpath(data, "reference_apparatus_A1_matrices.npz"))
ly = npzread(joinpath(data, "timing_lyapunov_matrices.npz"))
auth = npzread(joinpath(data, "authoritative_center_maps.npz"))
A = Float64.(a1["A_flow"]); B = Float64.(a1["B_q_flow"])
JF = Float64.(a1["J_fast"]); JS = Float64.(a1["J_source"]); Knom = Float64.(a1["K_nominal"])
scales = Vector{Float64}(ly["state_scales"])
Fcell_authoritative = Float64.(auth["F_cell_centers"])
Fedge_authoritative = Float64.(auth["F_edge_centers"])
edges = Int.(auth["edges"])
nx, nq, nz = 32, 3, 35
Az = zeros(nz, nz); Az[1:nx, 1:nx] .= A; Az[1:nx, nx+1:end] .= B

domains = JSON3.read(read(joinpath(data, "edge_conditioned_domains.json"), String))
event_cells = JSON3.read(read(joinpath(data, "event_cells.json"), String))
events_by_id = Dict(Int(c["id"]) => c["events"] for c in event_cells["cells"])

function parse_rational_enclosure(s)
    text = String(s)
    q = if occursin("/", text)
        p = split(text, "/")
        parse(BigInt, p[1]) // parse(BigInt, p[2])
    else
        parse(BigInt, text) // BigInt(1)
    end
    x = Float64(q)
    return interval(prevfloat(x), nextfloat(x))
end

function thin_interval_matrix(M)
    return IntervalMatrix([interval(Float64(M[i,j])) for i in axes(M,1), j in axes(M,2)])
end

function interval_identity(n)
    return IntervalMatrix([interval(i == j ? 1.0 : 0.0) for i in 1:n, j in 1:n])
end

function interval_radius_about(Fint, C)
    R = zeros(size(C))
    contained = true
    for i in axes(C,1), j in axes(C,2)
        d = Fint[i,j] - interval(C[i,j])
        R[i,j] = nextfloat(max(abs(inf(d)), abs(sup(d))))
        contained &= (C[i,j] in Fint[i,j])
    end
    return R, contained
end

function rigorous_delta_bound(R)
    # ||Delta||_2 <= sqrt(||R||_1 ||R||_inf), with outward interval sums.
    row_bounds = [sum(interval(R[i,j]) for j in axes(R,2)) for i in axes(R,1)]
    col_bounds = [sum(interval(R[i,j]) for i in axes(R,1)) for j in axes(R,2)]
    row_max = maximum(sup(x) for x in row_bounds)
    col_max = maximum(sup(x) for x in col_bounds)
    return sup(sqrt(interval(row_max) * interval(col_max)))
end

JF_I, JS_I = thin_interval_matrix(JF), thin_interval_matrix(JS)
alg = ScaleAndSquare(8, 12)

function enclose_map(events, dwell_ranges, C_authoritative)
    M = interval_identity(nz)
    range_s = Any[]
    for j in eachindex(dwell_ranges)
        dlo_us = parse_rational_enclosure(dwell_ranges[j]["min_us"])
        dhi_us = parse_rational_enclosure(dwell_ranges[j]["max_us"])
        dt = hull(dlo_us, dhi_us) * interval(1e-6)
        push!(range_s, [inf(dt), sup(dt)])
        A_dt = IntervalMatrix([interval(Az[i,k]) * dt for i in 1:nz, k in 1:nz])
        E = exp(A_dt; alg=alg)
        M = E * M
        if j <= length(events)
            M = (String(events[j]["kind"]) == "F" ? JF_I : JS_I) * M
        end
    end
    Fint = Matrix{Interval{Float64}}(undef, nz, nz)
    for i in 1:nz, j in 1:nz
        Fint[i,j] = interval(0.0)
    end
    for i in 1:nx, j in 1:nz
        Fint[i,j] = M[i,j]
    end
    for i in 1:nq, j in 1:nx
        Fint[nx+i,j] = interval(Knom[i,j])
    end
    for i in 1:nz, j in 1:nz
        Fint[i,j] *= interval(scales[j] / scales[i])
    end
    R, contained = interval_radius_about(Fint, C_authoritative)
    return R, rigorous_delta_bound(R), contained, range_s, maximum(R)
end

Fcell_rad = zeros(13, nz, nz); delta_cell = zeros(13)
Fedge_rad = zeros(45, nz, nz); delta_edge = zeros(45)
cell_center_contained = falses(13)
edge_center_contained = falses(45)
edge_exact_domain_used = falses(45)
cell_records = Any[]; edge_records = Any[]

for rec in domains["cells"]
    cid = Int(rec["cell"])
    R, delta, contained, ranges, maxrad = enclose_map(events_by_id[cid], rec["dwell_ranges_us"], Fcell_authoritative[cid+1,:,:])
    Fcell_rad[cid+1,:,:] .= R; delta_cell[cid+1] = delta; cell_center_contained[cid+1] = contained
    push!(cell_records, Dict("cell"=>cid,"delta_2_bound"=>delta,"max_entry_radius"=>maxrad,"authoritative_center_contained"=>contained,"dwell_ranges_s"=>ranges))
end

for rec in domains["edges"]
    eid = Int(rec["edge_id"]); source = Int(rec["source"]); target = Int(rec["target"])
    R, delta, contained, ranges, maxrad = enclose_map(events_by_id[source], rec["dwell_ranges_us"], Fedge_authoritative[eid+1,:,:])
    Fedge_rad[eid+1,:,:] .= R; delta_edge[eid+1] = delta; edge_center_contained[eid+1] = contained; edge_exact_domain_used[eid+1] = true
    push!(edge_records, Dict("edge_id"=>eid,"source"=>source,"target"=>target,"delta_2_bound"=>delta,"max_entry_radius"=>maxrad,"authoritative_center_contained"=>contained,"dwell_ranges_s"=>ranges))
end

all_contained = all(cell_center_contained) && all(edge_center_contained)
all_exact_edges = all(edge_exact_domain_used)
all_deltas_valid = all(isfinite, delta_cell) && all(isfinite, delta_edge) && all(delta_cell .>= 0.0) && all(delta_edge .>= 0.0)
all_matrices_finite = all(isfinite, Fcell_authoritative) && all(isfinite, Fedge_authoritative) && all(isfinite, Fcell_rad) && all(isfinite, Fedge_rad)
fail_closed_ok = all_contained && all_exact_edges && all_deltas_valid && all_matrices_finite

npzwrite(joinpath(results, "interval_timing_enclosures.npz"), Dict(
    "F_cell_center"=>Fcell_authoritative,
    "F_cell_rad"=>Fcell_rad,
    "delta_cell_2_bound"=>delta_cell,
    "F_edge_center"=>Fedge_authoritative,
    "F_edge_rad"=>Fedge_rad,
    "delta_edge_2_bound"=>delta_edge,
    "edges"=>edges,
    "cell_center_contained"=>cell_center_contained,
    "edge_center_contained"=>edge_center_contained,
    "edge_exact_domain_used"=>edge_exact_domain_used,
    "all_authoritative_centers_contained"=>[all_contained],
))

report = Dict(
    "status"=>(fail_closed_ok ? "VALIDATED_INDEPENDENT_DWELL_INTERVAL_ENCLOSURES_GENERATED" : "FAIL_CLOSED_ENCLOSURE_VALIDATION_ERROR"),
    "model_scope"=>"frozen rounded canonical A1 linearized hybrid model and declared timing continuum",
    "method"=>"IntervalArithmetic.jl plus IntervalMatrices.jl interval matrix exponentials; exact rational domain vertices define outward dwell ranges",
    "matrix_exponential_algorithm"=>"ScaleAndSquare(8,12)",
    "cell_count"=>13,
    "edge_count"=>45,
    "state_dimension"=>35,
    "cell_records"=>cell_records,
    "edge_records"=>edge_records,
    "all_authoritative_centers_contained"=>all_contained,
    "all_edge_enclosures_use_exact_Theta_cd"=>all_exact_edges,
    "all_deltas_finite_and_nonnegative"=>all_deltas_valid,
    "all_interval_matrix_arrays_finite"=>all_matrices_finite,
    "fail_closed_checks_passed"=>fail_closed_ok,
    "limitation"=>"Each affine dwell is bounded separately. The enclosure is outward but loses the common dependence among T, phi_f, and phi_s and may be highly conservative.",
    "interpretation"=>"Failure of a later norm-ball robustification means only that this sufficient enclosure/robustification failed; it is not an instability result.",
)
open(joinpath(results, "interval_enclosure_report.json"), "w") do io
    JSON3.pretty(io, report)
end
println(JSON3.write(Dict("status"=>report["status"],"delta_cell_min"=>minimum(delta_cell),"delta_cell_max"=>maximum(delta_cell),"delta_edge_min"=>minimum(delta_edge),"delta_edge_max"=>maximum(delta_edge),"centers_contained"=>all_contained,"exact_edge_domains"=>all_exact_edges,"fail_closed_checks_passed"=>fail_closed_ok)))
exit(fail_closed_ok ? 0 : 4)
