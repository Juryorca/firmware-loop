#!/usr/bin/env python3
"""Gin route extractor with group-prefix reconstruction (Go x86-64).

Resolves Go string (ptr,len) from LEAQ/MOVL and tracks gin RouterGroup
return values stored in stack slots, so nested Group("/x") prefixes are
composed. Prints: file:line METHOD fullpath [scopes] handler.
"""
import re
import subprocess
import sys

GIN='github.com/gin-gonic/gin.(*RouterGroup).'
METHODS={'GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS','Any','Static','StaticFS','StaticFile','handle'}

def sections(b):
    out=subprocess.run(['readelf','-S','-W',b],capture_output=True,text=True, check=False).stdout
    secs=[]
    for l in out.splitlines():
        m=re.match(r'\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)',l)
        if m: secs.append((m.group(1),int(m.group(2),16),int(m.group(3),16),int(m.group(4),16)))
    return secs

def vread(b,secs,addr,n):
    if n<=0: return b''
    for name,va,off,size in secs:
        if va<=addr and addr+n<=va+size:
            with open(b,'rb') as f:
                f.seek(off+addr-va); return f.read(n)
    return None

def join(a,b):
    if not a: return b
    if not b: return a
    return a.rstrip('/')+'/'+b.lstrip('/') if not b.startswith('/') else a.rstrip('/')+b

def main():
    b=sys.argv[1]; pat=sys.argv[2] if len(sys.argv)>2 else r'http_service\.\(\*HttpService\)\.[A-Za-z]*([Pp]repare|[Bb]uild|[Rr]egister)[A-Za-z]*Router'
    secs=sections(b)
    syms=subprocess.run(['go','tool','nm',b],capture_output=True,text=True, check=False).stdout
    names=[]
    for l in syms.splitlines():
        p=l.split()
        if len(p)>=3 and p[1] in ('t','T') and re.search(pat,p[2]) and not p[2].endswith('-fm'):
            names.append(p[2])
    for name in sorted(set(names)):
        out=subprocess.run(['go','tool','objdump','-s','^'+re.escape(name)+'$',b],capture_output=True,text=True, check=False).stdout
        if not out.strip(): continue
        lines=out.splitlines()
        # closure -> scope
        closure_scope={}
        for i,line in enumerate(lines):
            m=re.match(r'\s*(\S+?):(\d+)\s+0x([0-9a-f]+)\s+([0-9a-f]+)\s+(.*)',line)
            if not m: continue
            src,ins=m.group(1),m.group(5).strip()
            m2=re.match(r'LEAQ\s+(\S+\.func\d+)\(SB\), \w+',ins)
            if m2 and src.startswith('scopes.go'):
                ln_=ptr_=None
                for j in range(i+1,min(i+14,len(lines))):
                    m3=re.match(r'\s*(\S+?):(\d+)\s+0x([0-9a-f]+)\s+([0-9a-f]+)\s+(.*)',lines[j])
                    if not m3: continue
                    ins3=m3.group(5).strip()
                    m4=re.match(r'MOVQ\s+\$0x([0-9a-f]+), 0x10\(',ins3)
                    if m4: ln_=int(m4.group(1),16)
                    m5=re.match(r'LEAQ\s+0x([0-9a-f]+)\(IP\), \w+',ins3)
                    if m5 and ln_ is not None:
                        addr=int(m3.group(3),16); nb=len(m3.group(4))//2; disp=int(m5.group(1),16)
                        if disp>=0x80000000: disp-=0x100000000
                        raw=vread(b,secs,addr+nb+disp,ln_)
                        if raw is not None: ptr_=raw.decode('utf-8','replace')
                        break
                if ptr_ is not None: closure_scope[m2.group(1)]=ptr_
        print(f"\n## {name}")
        regs={}; slot_prefix={}; started=False
        pending_group=None; ax_prefix=None; ax_source=None
        recent=[]
        for line in lines:
            if line.startswith('TEXT '):
                if started: break
                started=True; continue
            if not started: continue
            m=re.match(r'\s*(\S+?):(\d+)\s+0x([0-9a-f]+)\s+([0-9a-f]+)\s+(.*)',line)
            if not m: continue
            src,ln,addr,bytestr,ins=m.group(1),int(m.group(2)),int(m.group(3),16),m.group(4),m.group(5).strip()
            nb=len(bytestr)//2
            # string operands
            m2=re.match(r'LEAQ\s+0x([0-9a-f]+)\(IP\), (\w+)',ins)
            if m2:
                disp=int(m2.group(1),16)
                if disp>=0x80000000: disp-=0x100000000
                regs[m2.group(2)]=('mem',addr+nb+disp); recent.append(('lea',m2.group(2),addr+nb+disp))
            m3=re.match(r'LEAQ\s+(\S+)\(SB\), (\w+)',ins)
            if m3:
                regs[m3.group(2)]=('sym',m3.group(1)); recent.append(('sym',m3.group(2),m3.group(1)))
            m4=re.match(r'MOV[QL]\s+\$0x([0-9a-f]+), (\w+)',ins)
            if m4:
                regs[m4.group(2)]=('imm',int(m4.group(1),16)); recent.append(('imm',m4.group(2),int(m4.group(1),16)))
            # slot loads: MOVQ 0xNN(SP), REG
            m5=re.match(r'MOVQ\s+0x([0-9a-f]+)\(SP\), (\w+)',ins)
            if m5:
                off=int(m5.group(1),16); r=m5.group(2); regs[r]=('slot',off)
                if r=='AX': ax_source=('slot',off); ax_prefix=None
            m5b=re.match(r'MOVQ\s+0x([0-9a-f]+)\((?!SP\))(\w+)\), AX',ins)
            if m5b:
                ax_source=('mem',); ax_prefix=None
            # slot stores: MOVQ REG, 0xNN(SP)
            m6=re.match(r'MOVQ\s+(\w+), 0x([0-9a-f]+)\(SP\)',ins)
            if m6:
                r,off=m6.group(1),int(m6.group(2),16)
                if pending_group is not None and r=='AX':
                    slot_prefix[off]=pending_group; pending_group=None
            # AX clobber detection (destination register AX)
            if re.match(r'(LEAQ|MOV[QL]|XOR[LQ]|MOVL|MOVQ).*,\s*AX\s*$',ins) and not m5b and not m5:
                ax_source='other'; ax_prefix=None
            # calls
            m7=re.match(r'CALL\s+'+re.escape(GIN)+r'(\w+)\(SB\)',ins)
            if m7:
                meth=m7.group(1)
                if meth=='Group':
                    # receiver slot = last slot loaded into AX
                    if ax_source and ax_source[0]=='slot': par=slot_prefix.get(ax_source[1],'')
                    elif ax_source=='group' and ax_prefix is not None: par=ax_prefix
                    elif ax_source and ax_source[0]=='mem': par=''
                    else: par=''
                    ptr=regs.get('BX'); l=regs.get('CX')
                    path=''
                    if ptr and ptr[0]=='mem' and l and l[0]=='imm':
                        raw=vread(b,secs,ptr[1],l[1] or 0)
                        if raw is not None: path=raw.decode('utf-8','replace')
                    full=join(par,path) if path else par
                    mws=[]
                    for e in recent[-14:]:
                        if e[0]=='sym' and e[2].startswith('unifi-access-be/coms/http/middleware.'):
                            f=e[2]
                            mws.append(f.split('/')[-1].split('.')[-1]+('['+closure_scope[f]+']' if f in closure_scope else ''))
                    print(f"  {src}:{ln} GROUP {full!r} mw={mws}")
                    pending_group=full; ax_prefix=full; ax_source='group'; recent=[]
                elif meth in METHODS:
                    def gs(rp,rl, regs=regs):
                        p=regs.get(rp); l=regs.get(rl)
                        if p and p[0]=='mem' and l and l[0]=='imm':
                            raw=vread(b,secs,p[1],l[1] or 0)
                            if raw is not None: return raw.decode('utf-8','replace')
                        return ''
                    if meth=='handle':
                        path=gs('DI','SI'); real=gs('BX','CX')
                    else:
                        path=gs('BX','CX'); real=meth
                    if ax_source and ax_source[0]=='slot': par=slot_prefix.get(ax_source[1],'')
                    elif ax_source=='group' and ax_prefix is not None: par=ax_prefix
                    elif ax_source and ax_source[0]=='mem': par=''
                    else: par=''
                    full=join(par,path)
                    funcs=[]
                    for e in recent[-30:]:
                        if e[0]=='sym' and e[2].startswith('unifi-access-be/') and e[2]!=name:
                            f=e[2]
                            if (('http_service.(*HttpService).prepare' in f
                                 or 'http_service.(*HttpService).build' in f)
                                    and f not in closure_scope):
                                continue
                            if f not in [x[0] for x in funcs]:
                                funcs.append((f, closure_scope.get(f)))
                    fs='; '.join((f.split('/')[-1]+('['+sc+']' if sc else '')) for f,sc in funcs[-6:])
                    print(f"  {src}:{ln} {real:6} {full}  {fs}")
                    recent=[]
            if len(recent)>60: recent=recent[-60:]
            if pending_group is not None and len(recent)>40 and not any(e[0]=='sym' and 'Group' in str(e[2]) for e in recent[-3:]):
                pass
    return 0

if __name__=='__main__': sys.exit(main())
