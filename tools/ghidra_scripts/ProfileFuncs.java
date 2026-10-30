// ProfileFuncs.java — batch per-function capability profile for a binary.
// For every function, flag whether it calls any of a set of "interesting"
// callees (auth/role gates, precondition checks, dangerous sinks) and count
// how many request parameters it reads. Output TSV, one row per function.
//
// Usage: -postScript ProfileFuncs.java <outDir> [gateA,gateB,...] [sinkA,sinkB,...]
//   (gate/sink lists are comma-separated SUBSTRINGS matched against callee names)
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;

public class ProfileFuncs extends GhidraScript {
    private static String[] DEF_GATES = {
        "Auth", "auth", "User_Belongs", "Is_User_Admins", "Role_Is_User_Delegated",
        "naspriv", "Is_Admin", "Check_Session", "Verify_", "Is_Website_Enabled",
        "Is_Webfs_Enabled", "Privilege"
    };
    private static String[] DEF_SINKS = {
        "system", "popen", "qnap_exec", "qnap_popen", "execv", "execve", "execl",
        "sqlite3_exec", "sqlite3_prepare", "Remote_Folder", "curl_"
    };

    public void run() throws Exception {
        String[] args = getScriptArgs();
        String outDir = args[0];
        String[] gates = args.length > 1 && !args[1].isEmpty() ? args[1].split(",") : DEF_GATES;
        String[] sinks = args.length > 2 && !args[2].isEmpty() ? args[2].split(",") : DEF_SINKS;
        Program prog = getCurrentProgram();
        FunctionManager fm = prog.getFunctionManager();
        FunctionIterator fi = fm.getFunctions(true);
        File out = new File(outDir, prog.getName() + ".profile.tsv");
        PrintWriter w = new PrintWriter(new BufferedWriter(new FileWriter(out)));
        w.println("func\taddr\tsize\tn_calls\tn_params\tgates\tsinks");
        int n = 0;
        while (fi.hasNext()) {
            Function f = fi.next();
            n++;
            String name = f.getName();
            int nCalls = 0, nParams = 0;
            TreeSet<String> hitG = new TreeSet<>(), hitS = new TreeSet<>();
            InstructionIterator ii = prog.getListing().getInstructions(f.getBody(), true);
            while (ii.hasNext()) {
                Instruction ins = ii.next();
                if (ins.getFlowType().isCall()) {
                    nCalls++;
                    Address[] flows = ins.getFlows();
                    for (Address t : flows) {
                        String cn = calleeName(prog, t);
                        if (cn == null) continue;
                        if (cn.contains("CGI_Find_Parameter") || cn.contains("CGI_Get_Parameter")) nParams++;
                        for (String g : gates) if (cn.contains(g)) hitG.add(g);
                        for (String s : sinks) if (cn.contains(s)) hitS.add(s);
                    }
                }
            }
            w.println(String.join("\t", name, f.getEntryPoint().toString(),
                    Integer.toString((int) f.getBody().getNumAddresses()),
                    Integer.toString(nCalls), Integer.toString(nParams),
                    String.join("/", hitG), String.join("/", hitS)));
        }
        w.close();
        println("ProfileFuncs: " + n + " funcs -> " + out.getAbsolutePath());
    }

    private String calleeName(Program prog, Address t) {
        Function cf = prog.getFunctionManager().getFunctionAt(t);
        if (cf != null) return cf.getName();
        Symbol s = prog.getSymbolTable().getPrimarySymbol(t);
        if (s != null) return s.getName();
        // thunk -> referenced external
        Data d = prog.getListing().getDataAt(t);
        if (d != null && d.isPointer()) {
            Symbol ps = prog.getSymbolTable().getPrimarySymbol((Address) d.getValue());
            if (ps != null) return ps.getName();
        }
        return null;
    }
}
