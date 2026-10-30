// Dump decompiled C for functions that reference a given string/data address.
// Usage: -postScript DumpFuncsWithStr.java <outDir> <hexAddr> [hexAddr2 ...]
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class DumpFuncsWithStr extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String outDir = args[0];
        Program prog = getCurrentProgram();
        DecompInterface di = new DecompInterface();
        di.openProgram(prog);
        FunctionManager fm = prog.getFunctionManager();
        Set<Function> funcs = new LinkedHashSet<>();
        ReferenceManager rm = prog.getReferenceManager();
        for (int i = 1; i < args.length; i++) {
            Address a = prog.getAddressFactory().getDefaultAddressSpace().getAddress(args[i]);
            ReferenceIterator it = rm.getReferencesTo(a);
            while (it.hasNext()) {
                Reference r = it.next();
                Function f = fm.getFunctionContaining(r.getFromAddress());
                if (f != null) funcs.add(f);
            }
        }
        File out = new File(outDir, prog.getName() + ".strrefs.c");
        PrintWriter fw = new PrintWriter(new BufferedWriter(new FileWriter(out)));
        fw.println("/* refs to " + Arrays.toString(Arrays.copyOfRange(args,1,args.length)) + " */");
        for (Function f : funcs) {
            fw.println("/* ==== FUNC " + f.getName() + " @" + f.getEntryPoint() + " ==== */");
            DecompileResults res = di.decompileFunction(f, 90, monitor);
            if (res != null && res.decompileCompleted())
                fw.println(res.getDecompiledFunction().getC());
            else
                fw.println("/* DECOMP FAILED */");
        }
        fw.close();
        println("DumpFuncsWithStr: " + funcs.size() + " funcs -> " + out.getAbsolutePath());
    }
}
