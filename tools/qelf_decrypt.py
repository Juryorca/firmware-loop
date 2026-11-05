#!/usr/bin/env python3
# QNAP QELF (.text) decrypter — recovers the reused 4KB ChaCha keystream by
# many-time-pad over the encrypted pre-auth CGIs, then decrypts .text.
# Scheme (per TeamT5 "Decryption of QNAP encrypted pre-auth CGIs (qelf)"):
#   .text encrypted page-by-page; per 64-byte chunk counter resets to 0 at the
#   page's processing start => same 4KB keystream reused on every page/file.
#   Page 0 is partial (starts at .text offset); pages>=1 are 4096-aligned.
import collections
import glob
import math
import os
import struct
import sys

from capstone import *


def shdr(d, name):
    e_shoff=struct.unpack_from('<Q',d,40)[0]; e_shentsize=struct.unpack_from('<H',d,58)[0]
    e_shnum=struct.unpack_from('<H',d,60)[0]; e_shstrndx=struct.unpack_from('<H',d,62)[0]
    shstr=struct.unpack_from('<Q',d,e_shoff+e_shstrndx*e_shentsize+24)[0]
    for i in range(e_shnum):
        o=e_shoff+i*e_shentsize; no=struct.unpack_from('<I',d,o)[0]
        end=d.find(b'\x00',shstr+no)
        if d[shstr+no:end].decode('utf-8','replace')==name:
            return o, struct.unpack_from('<Q',d,o+24)[0], struct.unpack_from('<Q',d,o+32)[0]
    return None,None,None

def is_qelf(d):
    return d[:4]==b'\x7fELF' and struct.unpack_from('<I',d,0x30)[0]==0x40000000

def recover_keystream(enc_files, model_files):
    mod=[collections.Counter() for _ in range(4)]
    for f in model_files:
        d=open(f,'rb').read()
        if not is_qelf(d) and d[:4]==b'\x7fELF':
            _,off,sz=shdr(d,'.text')
            if off:
                b=d[off:off+sz]
                for i,x in enumerate(b): mod[i&3][x]+=1
    logp=[[math.log((m.get(b,0)+1)/sum(m.values())) for b in range(256)] for m in mod]
    pages=[]
    for f in enc_files:
        d=open(f,'rb').read(); _,off,sz=shdr(d,'.text'); p=off&~0xfff
        while p+4096<=off+sz:
            if p>=off: pages.append(d[p:p+4096])
            p+=4096
    K=bytearray(4096)
    for i in range(4096):
        lp=logp[i&3]; best=-1e18; bk=0
        for k in range(256):
            s=0.0
            for pg in pages: s+=lp[pg[i]^k]
            if s>best: best=s; bk=k
        K[i]=bk
    return K

def decrypt_file(path, K, out=None):
    d=bytearray(open(path,'rb').read())
    if not is_qelf(d): return None, 'not qelf'
    eo,off,sz=shdr(d,'.text')
    start_page=off&~0xfff
    # page 0 (partial): keystream index = X - off ; pages>=1: X & 0xfff
    p=start_page
    while p<off+sz:
        end=min(p+4096, off+sz)
        s=max(p, off)
        for X in range(s,end):
            if p==start_page:
                d[X]^=K[(X-off)&0xffff]   # first (partial) page
            else:
                d[X]^=K[X&0xfff]
        p+=4096
    struct.pack_into('<I', d, 0x30, 0)                      # e_flags = 0
    if eo is not None: struct.pack_into('<Q', d, eo+8, 0x6) # .text sh_flags
    out=out or (path+'.dec')
    open(out,'wb').write(d)
    return out, None

if __name__=='__main__':
    base=sys.argv[1] if len(sys.argv)>1 else '.'
    cfgs=sorted(glob.glob(os.path.join(base,'**','*.cgi'),recursive=True))
    enc=[f for f in cfgs if is_qelf(open(f,'rb').read())]
    print('encrypted:',len(enc))
    K=recover_keystream(enc, cfgs)
    open(os.path.join(base,'qelf_keystream.bin'),'wb').write(K)
    md=Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    for f in enc:
        o,err=decrypt_file(f,K)
        d=open(o,'rb').read(); _,off,sz=shdr(d,'.text')
        dec=d[off:off+sz]; ins=list(md.disasm(dec,0))
        print('%-40s -> %s  decode %.1f%%'%(f,o,100*len(ins)/(len(dec)//4)))
