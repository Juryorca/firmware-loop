// Dump decompiled C for every function in the current program.
// Usage: -postScript DumpDecomp.java <outDir> [nameFilterSubstr]
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import java.io.*;

public class DumpDecomp extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String outDir = args[0];
        String filter = args.length > 1 ? args[1] : null;
        Program prog = getCurrentProgram();
        DecompInterface di = new DecompInterface();
        di.toggleCCode(true);
        di.toggleSyntaxTree(true);
        di.openProgram(prog);
        FunctionManager fm = prog.getFunctionManager();
        FunctionIterator fi = fm.getFunctions(true);
        File out = new File(outDir, prog.getName() + ".c");
        PrintWriter fw = new PrintWriter(new BufferedWriter(new FileWriter(out)));
        int n = 0, ok = 0;
        while (fi.hasNext()) {
            Function f = fi.next();
            n++;
            if (filter != null && !f.getName().contains(filter)) continue;
            DecompileResults res = di.decompileFunction(f, 90, monitor);
            if (res != null && res.decompileCompleted()) {
                fw.println("/* ==== FUNC " + f.getName() + " @" + f.getEntryPoint() + " ==== */");
                fw.println(res.getDecompiledFunction().getC());
                ok++;
            } else {
                fw.println("/* ==== FUNC " + f.getName() + " @" + f.getEntryPoint() + " DECOMP FAILED ==== */");
            }
        }
        fw.close();
        println("DumpDecomp: " + ok + "/" + n + " functions -> " + out.getAbsolutePath());
    }
}
