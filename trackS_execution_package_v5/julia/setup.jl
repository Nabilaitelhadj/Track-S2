using Pkg
Pkg.activate(@__DIR__)
Pkg.add(["JuMP", "NPZ", "JSON3", "Clarabel", "SCS", "IntervalArithmetic", "IntervalMatrices", "IntervalLinearAlgebra"])
if get(ENV, "INSTALL_MOSEKTOOLS", "0") == "1"
    Pkg.add("MosekTools")
end
Pkg.precompile()
