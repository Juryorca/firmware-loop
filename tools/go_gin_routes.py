#!/usr/bin/env python3
"""Extract gin route registrations from a Go binary using `go tool objdump`.

Usage: go_gin_routes.py <binary> [symbol-regex]
Resolves LEAQ x(IP) string operands (Go string = ptr+len) and prints
source-line, gin method, path, and nearby referenced funcs.
Requires: go tool in PATH, readelf.
"""
import subprocess, re, sys, struct

GIN = 'github.com/gin-gonic/gin.(*RouterGroup).'
METHODS = {'GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS','Any','Group','Use','Static','StaticFS','StaticFile','Handle','handle'}

def sections(b):
    out = subprocess.run(['readelf','-S','-W',b],capture_output=True,text=True).stdout
    secs=[]
    for l in out.splitlines():
        m=re.match(r'\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)',l)
        if m:
            secs.append((m.group(1),int(m.group(2),16),int(m.group(3),16),int(m.group(4),16)))
    return secs

def vread(b, secs, addr, n):
    for name,va,off,size in secs:
        if va<=addr and addr+n<=va+size:
            with open(b,'rb') as f:
                f.seek(off+(addr-va)); return f.read(n)
    return None

def main():
    b=sys.argv[1]; pat=sys.argv[2] if len(sys.argv)>2 else r'http_service\.\(\*HttpService\)\.(prepare|build).*Router'
    secs=sections(b)
    syms=subprocess.run(['go','tool','nm',b],capture_output=True,text=True).stdout
    names=[l.split()[2] for l in syms.splitlines() if len(l.split())>=3 and re.search(pat,l.split()[2]) and l.split()[1] in ('t','T')]
    names=[n for n in names if not n.endswith('-fm')]
    for name in sorted(set(names)):
        out=subprocess.run(['go','tool','objdump','-s','^'+re.escape(name)+'$',b],capture_output=True,text=True).stdout
        if not out.strip(): continue
        print(f"\n### {name}")
        # pre-scan: map scopes.go closures -> captured scope string
        lines=out.splitlines()
        closure_scope={}
        for i,line in enumerate(lines):
            m=re.match(r'\s*(\S+?):(\d+)\s+0x([0-9a-f]+)\s+([0-9a-f]+)\s+(.*)',line)
            if not m: continue
            src,ins=m.group(1),m.group(5).strip()
            m2=re.match(r'LEAQ\s+(\S+\.func\d+)\(SB\), (\w+)',ins)
            if m2 and src.startswith('scopes.go'):
                ln_=None; ptr_=None
                for j in range(i+1,min(i+12,len(lines))):
                    m3=re.match(r'\s*(\S+?):(\d+)\s+0x([0-9a-f]+)\s+([0-9a-f]+)\s+(.*)',lines[j])
                    if not m3: continue
                    ins3=m3.group(5).strip()
                    m4=re.match(r'MOVQ\s+\$0x([0-9a-f]+), 0x10\(',ins3)
                    if m4: ln_=int(m4.group(1),16)
                    m5=re.match(r'LEAQ\s+0x([0-9a-f]+)\(IP\), \w+',ins3)
                    if m5 and ln_ is not None:
                        addr=int(m3.group(3),16); nb=len(m3.group(4))//2
                        disp=int(m5.group(1),16)
                        if disp>=0x80000000: disp-=0x100000000
                        raw=vread(b,secs,addr+nb+disp,ln_)
                        if raw is not None: ptr_=raw.decode('utf-8','replace')
                        break
                if ptr_ is not None: closure_scope[m2.group(1)]=ptr_
        regs={}; lastcall=None; recent=[]; started=False
        for line in out.splitlines():
            if line.startswith('TEXT '):
                if started: break
                started=True; continue
            if not started: continue
            m=re.match(r'\s*(\S+?):(\d+)\s+0x([0-9a-f]+)\s+([0-9a-f]+)\s+(.*)',line)
            if not m: continue
            src,ln,addr,bytestr,instr=m.group(1),int(m.group(2)),int(m.group(3),16),m.group(4),m.group(5).strip()
            nbytes=len(bytestr)//2
            # LEAQ disp(IP), reg
            m2=re.match(r'LEAQ\s+0x([0-9a-f]+)\(IP\), (\w+)',instr)
            if m2:
                disp=int(m2.group(1),16); reg=m2.group(2)
                # sign-extend 32-bit displacement
                if disp>=0x80000000: disp-=0x100000000
                tgt=addr+nbytes+disp
                regs[reg]=('mem',tgt)
                recent.append((ln,'lea',m2.group(2),tgt))
            m3=re.match(r'LEAQ\s+(\S+)\(SB\), (\w+)',instr)
            if m3:
                regs[m3.group(2)]=('sym',m3.group(1)); recent.append((ln,'sym',m3.group(2),m3.group(1)))
            m4=re.match(r'MOVL\s+\$0x([0-9a-f]+), (\w+)',instr)
            if m4:
                regs[m4.group(2)]=('imm',int(m4.group(1),16))
            m5=re.match(r'CALL\s+'+re.escape(GIN)+r'(\w+)\(SB\)',instr)
            if m5:
                meth=m5.group(1)
                if meth in METHODS:
                    def getstr(rp, rl):
                        ptr=regs.get(rp); ln_=regs.get(rl)
                        if ptr and ptr[0]=='mem' and ln_ and ln_[0]=='imm':
                            raw=vread(b,secs,ptr[1],ln_[1] or 0)
                            if raw is not None: return raw.decode('utf-8','replace')
                        return None
                    if meth=='handle':
                        path=getstr('DI','SI'); real=getstr('BX','CX')
                    else:
                        path=getstr('BX','CX'); real=meth
                    funcs=[]
                    for (sl,k,r,v) in recent[-45:]:
                        if k=='sym' and v not in funcs and not v.startswith('type:') and 'runtime.' not in v and 'gin-gonic' not in v:
                            funcs.append(v)
                    ann=[]
                    for f in funcs[-8:]:
                        short=f.split('/')[-1]
                        ann.append(short+(' [scope='+closure_scope[f]+']' if f in closure_scope else ''))
                    print(f"  {src}:{ln} {str(real):6} path={path!r} funcs={ann}")
                recent=[]
            if len(recent)>80: recent=recent[-80:]

if __name__=='__main__': main()
