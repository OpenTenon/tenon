"""Actual process/OS leases, pinned installers and cache reclamation boundaries."""
from __future__ import annotations
import ast
import ctypes
import csv
import hashlib
import io
import json
import marshal
import os
from pathlib import Path
import queue
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

from test_engineering import RepositoryFixture, ROOT
from test_entrypoint import Backend
from engineering_common import write_json
from task_store import TaskStore
import controller_leases as leases
import maintenance
import tenon


def snapshot(root):
    return {path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def packet(process, timeout=20):
    inbox = queue.Queue()
    threading.Thread(target=lambda: inbox.put(process.stdout.readline()), daemon=True).start()
    raw = inbox.get(timeout=timeout)
    if not raw:
        raise AssertionError(process.stderr.read().decode(errors="replace"))
    return json.loads(raw)


class NativeReadLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tenon-controller-native-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "static.lock"
        self.path.write_bytes(b'\n{"probe":1}')
        self.path.chmod(stat.S_IREAD)
        self.addCleanup(lambda: self.path.chmod(stat.S_IREAD | stat.S_IWRITE) if self.path.exists() else None)

    def test_readonly_shared_handles_and_exclusive_probe_preserve_all_bytes(self):
        before = snapshot(self.root)
        with leases.ReadLock(self.path) as first, leases.ReadLock(self.path) as second:
            self.assertTrue(first.locked and second.locked)
            with self.assertRaises(leases.ControllerBusy) as raised:
                with leases.ReadLock(self.path, exclusive=True, timeout=0):
                    pass
            if os.name == "nt":
                self.assertEqual(raised.exception.winerror, 33)
            from task_queue import classify
            self.assertEqual(classify(raised.exception), "RESOURCE")
            self.assertEqual(first.stream.readable(), True)
            self.assertEqual(first.stream.writable(), False)
        with leases.ReadLock(self.path, exclusive=True, timeout=0):
            with self.assertRaises(leases.ControllerBusy):
                with leases.ReadLock(self.path, timeout=0):
                    pass
        self.assertEqual(snapshot(self.root), before)

    def test_missing_or_invalid_metadata_is_not_created_repaired_or_treated_as_busy(self):
        missing = self.root / "missing.lock"
        with self.assertRaises(ValueError):
            leases.ReadLock(missing).__enter__()
        self.assertFalse(os.path.lexists(missing))
        self.path.chmod(stat.S_IREAD | stat.S_IWRITE)
        self.path.write_bytes(b"\nnot json")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            leases.ReadLock(self.path).__enter__()
        self.assertEqual(self.path.read_bytes(), before)
        error = PermissionError("native access denied in a path named timeout")
        error.winerror = 5
        from task_queue import classify
        self.assertEqual(classify(error), "POLICY")

    def test_independent_readers_and_exit_release_are_os_owned(self):
        code = """import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from controller_leases import ReadLock
with ReadLock(Path(sys.argv[2])):
 print(json.dumps({'pid':os.getpid()}),flush=True)
 sys.stdin.readline()
 if sys.argv[3]=='exit': os._exit(41)
"""
        process = subprocess.Popen([sys.executable, "-B", "-c", code, str(ROOT / "scripts"), str(self.path), "exit"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            value = packet(process)
            self.assertNotEqual(value["pid"], os.getpid())
            with leases.ReadLock(self.path):
                with self.assertRaises(leases.ControllerBusy):
                    leases.ReadLock(self.path, exclusive=True, timeout=0).__enter__()
            process.communicate(input=b"exit\n", timeout=10)
            self.assertEqual(process.returncode, 41)
            with leases.ReadLock(self.path, exclusive=True, timeout=2):
                pass
        finally:
            if process.poll() is None:
                process.communicate(input=b"release\n", timeout=10)


BODY = '''
if __name__ == "__main__":
    import json, os, subprocess, sys, time
    from pathlib import Path
    args = sys.argv[1:]
    if "commit" in args:
        repo = Path(args[args.index("--repo") + 1])
        (repo / "bound-commit.txt").write_text("authorized fixture change", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "--", "bound-commit.txt"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "bound fixture commit"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(json.dumps({"pid":os.getpid(), "author":subprocess.check_output(["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>"]).decode().strip(),
            "credential_helper":subprocess.check_output(["git", "-C", str(repo), "config", "--get", "credential.helper"]).decode().strip()}), flush=True)
        raise SystemExit(0)
    if "--marker" in args:
        Path(args[args.index("--marker") + 1]).write_text("business executed", encoding="utf-8")
    if "spawn" in args:
        release = args[args.index("--release") + 1]
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("engineering_drills.py")), "hold", "--release", release], stdout=subprocess.PIPE)
        data = json.loads(child.stdout.readline())
        print(json.dumps({"pid":os.getpid(),"spawned_child_pid":child.pid,"child":data}), flush=True)
        os._exit(42)
    print(json.dumps({"pid":os.getpid(),"argv":args}), flush=True)
    if "hold" in args:
        release = Path(args[args.index("--release") + 1])
        deadline = time.monotonic() + 120
        while not release.exists():
            if time.monotonic() >= deadline: raise SystemExit(9)
            time.sleep(0.01)
'''


# Exact immutable pre-ABI scripts; hermetic even in shallow CI checkouts.
LEGACY_SOURCE_B85 = (
    'c-q{(S99vxwl4ZtcCFWa>J$RwF?6c#gTdrrlZ4RAErJ0V5#|JpVb}TZw}l=Rus!D5H&v}wa~Oow)kBM4``Ul~YrfxZ!_&Y1*MI!$v5=oF9*gsQZo16poZs_b'
    '%U^G~xtq_s1^3Z?`~8-8=kv$nzy8O6{A(FTZ%6pifBoxiw+eS}Z-0lo)&Gh2+VB47;Y#1krpLF(2fTCRTKQP`-D^AFJj_Bjn(Zd5c|FQaYwo#o*@um=Ih@w2'
    '*I?XR&GW7^-*%%$EjY}!i)g+pU8mz>ZhpP2CR^8;ZTnGBE=6xO?|tF-53~HkYBDZv8nw@lNxph))PrcgjkeQqcfF`ZAG5F&Ew<4n7#H7z!2>+2(F%6WkJ+HK'
    '^z(tUsC(DIbC-=;beh-P>!Ms*&$h*nMXj2fj@|S8vTset&Sh3;<{Gv2dY)e&r{jk4+2ICbF#dLL<*mBcZ8~0@C*zC$zTSm#o}$Im;V|<{AM+i&bHNMe^(K7o'
    '-R2klIgI}l^o+lK?+`p4w)3rfYSjAhejbd`iHxytCVoGn-wiJOd;jn;+m7TrU*P-wtQLjFZ+iGyd36{JbMC0|oLi0X-OFp7pTn%?E&c1}Jl}foo}c+(usXDN'
    'r8WG0Uevp@j+bkVa@|IM<dl3TsE(p;bC`Qry$+r}%TagK^ZMQ>=MC|`Z`Wq8=XHmT@Um)bg3@4g3ESnTu($bq?d49@ez|dMl$$R@r@tIU-b-VbYr(i+?I!T<'
    '{dYeP>vCNk8s*%7!aU~Y@B3Dx{IqJ+n-Q)fthMp}#q+E%-y3sq#(T5|#RKfIEv&_9;`jC~_+J_KR9Lz$#>M4y3?B@hPOo9<wx~BA%OTvqT)GB+ANJ+LDuB5O'
    'aNmtb=h@aRwCeb8?{StdM(v%Ko8Vj(`qyAb*X$di7c|GYD5&<LLD}mzy-o=C|GQCcoEjngx9&Oc%rmX+Gd{bx5B&A)<$b*<ulB8==51lE4*~3rMR`r@_Y(4&'
    'rHnH_8Gky#;K?=i>KXpK?!1rkt@1|9*W$3KtyXhd=i<k_=AB#R=5-P_4vkv%MsvIM*0Z6QU;2Lu=B%-r<kxTpkE`kUVKs%ho`>t>WbA}Nt@5aTHz>kcu5Mb-'
    'l}qQnw`zq?pPlDMt_6D%o)NaJ`||%6+v;&K#x>$KY}LAZSdYJ9edv3*Z*Ji?&v3riv$_|xw`-6Aa$Z&kHCOe;702c0>0IvV9iR7NPhaG79^ssZ(=Eu5@@CZ@'
    'KCFgY_Z-M^)k|=;R_8g%q++hUb^Z=&(X~~V`@Z9P^`?Vo4A$xZ>%E-ikK!{q$Nh-T?Y4S-!}SpNT;xISRb7w?PSg^g&9+t8ue2x5*?VHOYF(eg_O5@Lmm3GM'
    '-noF~%6h)roEL>AtgE+#|3AQ9`3v^>xxMW>FpgVG&Re;3Kp8sQ_1q4eLwLU%%qz@S^)|^zhu0Ba*Nu3OHs`Yt&Lr%QN#5On9NxVSn$Ds&IlW|z0p{m2t5reP'
    '@?JI0*KP{u4P@8H!dQ<M%ui70@ZJe|Ehf7N&R8dL_AEJhExu1d2TUEfpWnYM;NIhF6Q4mcr9E)Yv#awl$Mcylu4iTEILmkco?lVMG)_JIFFkX8o#kEHn>?28'
    '0_NzpsKMUeHQd-UdjqdjHQwnqD|lD<ZV7t;)^ZekKUnu_^L6O-SZ2X~ofLZFy`E6-H70&{4}0Z08F&9i{nr~6E3p2s$C_)@69LQ-$eD9{9PQx0q$7iEbcFeV'
    'b%|hI;Jm|sgDZ}?x`XdO;2gmBq$gpHs%JbSZPa`#K1;OU{0_+I(k*~_3t&&S;2md$9?IY2{OW-I{EXyWy@mYWHt1A1|8QUVw+lMlyUOd@WAA~-GdkgXz&?ZX'
    '(gS%{yv=K$2iTKgBf4A~;YRMAcUYSoo-x!9FxJ}?uA78K_;2Of9ZvT0{0H#vAm`VME9_sI_a^+Vx979NvPt|Kzo+vxhie1zZ(HW@XZTDwp9MH05$<o$Z6WNX'
    'ZF{&1<#P>rfb&IrrBVLP&oZy6bw6f1I#*hT(Rkq;)f%Dy{AAy=7Hy?WnEGAVKhX*F@_7pS?PaxJ+48ZJoA}KgtQC%d&Q*KYjNp797u(U^x-VZ(?+xn-HURAD'
    'yazHYy7`4>G{b%A2a#Xse~2}=bbFd?7CLqf<BS?rmLq+?d4)OOH=-__zb2gb((P^RfW9fh`+6Wtsu$d2i`qIu{S3Co4dgP|F`Gx&D<I3nx+KT0_5|qD3+dYd'
    '?wf8f9DO#2o723*zyE;qNP33nO~{);F`8|=a0Wp4f<6wy+=Je4PqF#O{ke!1Wv~exm-am9$j8LB+2h*d`9*uGz3c8L!-mrh*K)0JUe`g@4c|~t!n(rPK!$=%'
    'TR@q<G4~^^i@hJ{j1N-xLi+q{g66Q|$+<}zm)bXzaepafERN$0@&m@(1!K#er(pbf3sUw2oRu@^m@~|qGq17Sn}p5o0BpO#s8~1mI^263glLZ}@ch&HLHW#|'
    'OSu7l0qIGy|G<{4R>(#Sb6_LMdFre@+)H@Q{1VAG(Dk5SPiS*QZ11eV7x=u*t}sUh@B?<R|Il~XR^O+2Z$mx>>`jpG>&dQ*XLTj)V$>UAtPgRU@|U$Aa6Si('
    '$N2le81ii&>;t$j+qdg#Z$z>W>{s58C<j5VALd}Mw4cm%ZNl%XAj?<bi>W6;_BXa*mx1pxl`<FCwJ<+TK<|((0(#hxUtpV~4HjKsEsyrN^<4P{xhL>FwA=z&'
    '{$T6{`}bS0wYI@h+LDd;eX6|XIoy1CulcTD#Cw2G_c1H4?RSQEn4r#=dWGz$F3PUM?A^(C2J&-LPYRuNbJnxk1?F8ov*c<!8uTN`z#va&hV2KmJ>VTcU(H~='
    '?~7_K>0jX4tby-A`jmfTzK^m^P2Xju?AFi4I3m3bvU`TM=}hP=@o&^u^1fhKPsHa%lp!F)Kz4#12>03GzG52_L{@0adE+_X9vkaanrrkS;hI(0-&C7J_QT4<'
    'iu*jsd3+W(#aFND4!qGr%zmdatY^E;-X6DF!vpA9S3f8Cn1>~_Q&05Fx_-Yj-J|V({(amgY(%Bf$mxW<M%5$iCAxQ;<oYTkyR7crK#r_lhdJZ=JL4FDJO&#W'
    '=AhOaMID$gW$$5JV1e=(?0z`^;&0o2#dyWbD(<%&!%?L=99`keJwF}Qz2OXW1~|XX&M?<)_d#a7gilwLDZ`CB@|~tP#B=)GbjJlTr;hfE;r%}5RrdmC=N--('
    '*!gIqbV0|^n4HN@{e2$Q+)FTi;Qd`GqEGy2>?72t(k^mgd?;J&y=2?M;4eR*%?)Gf{yoh{F!yvWKSq^^_6+*L#(2P=iZ*CxH$r#B^4+a9qTDfE-<r?uZ@|vZ'
    'z54e1^ef(Y7<rvxnVz)?z$UqO4ce{~@YDL>1J3g!X@k;QTY47k@wb${ID`Aqd)7<4_<OZ=tmiz+MfE{0f@j(~{;dYr*L$mxoWtDb$1Hzx<`tCX?lnc%gU;^W'
    '#5mO&w|9&6)Q(-qco_Q4AG0yYz->>-HCzWDeec3zBVz|JUhs$JkButX9N-I490P1Q{9ORwRmmoQ89rTXT}5Nr%y-dpzVps-zYuib5w8{V3Vum<4?6E}KOf=q'
    'Fg}6tpb&J_X*LFb1osJybFl^A3G5-b=3}xwMBp2tjC`S3O7fW)H+&R6)M{Dg>+recyASy6kqm#<aUs~V#|8RfaQ?ujM*S-N#zR25#*nXY7NuX`*s6J?-^}vP'
    '3FC}-E^!|5TH4>WZXt;4H*G6O8Te?)U)=j!?*ZiHlJz!x&*u^4!Up68$1WrHhPDIvQ8$tk<x84vj`Ny_y}u6fBOWW-K3mXr^d6rd0Y1Nm|K;<I?=Y*mJIfBB'
    '=X<Up5A+&aXG7UVAcMQktlkrIjqgf68LlD4CEHsE?&;=WoemSQf5p9V-~Geb3dgVT9ok!xX`pWwA<Fm(_#7xJ=5;Uky)#|nIPVqqNO}DW)*XEc`YhOfu7g4+'
    'q&+-t>hrhh4!}ko^*#5c?=6-i@bP;&Z#Z(?`e@^Nqk4|?ILZqV+i`LKgN+q_CF{uM9+ZCZ-f8B1*y9Ta_5nN>$E5bCpG*2QGOibkS-?9+vv6(f;nMpe-*a&v'
    'xA|PTuoig{_dpro$~jD)!>?s^nj9WE{mRhsMzHt(YQdV({@!&&yix0{4Bsn<e&fBl<8wrDHucO!zKS}U_9E*KCF2hF?uGvFV*F|PS5nWrw!J}iJ=qnVa?EB3'
    'dH*h|F3fvBdSxFV<GETl#MW*k$KLnXm#Gi7>vM*G&i8?Fx);Bo=3uYywYm2@8AB5>MDpk6v#8%3j=WdT1D&Afbp<we1bY<x$L0xa9n3>)+R^7(`)aArkn+Na'
    'j|lmA%W1n4?hQKcWA>g4DRw`0odxL&LtYMZpPl#Kmvh<MR&T)wV^SXPxA*^+xdR;*-RxM9cxJiuI?Azbr+r?_)bok?Z*goAeAwm(+Lajl^=l8|x8oa1_h}zp'
    '+QP2`+qTCz1;&cu%$m<UXj>cCtv>_54Qy_fji5Ww=R61XPUzFQYkq*Qz2SVw=sbgKW?-Anw$XbFW9$R6ov*Z4%1<{Vu1{wT>`2fH3+-P<In*O6UV^ednF|o_'
    '_@wNhbKt{03Le@S*S7)t=Z4MmH|$qwlVJQ5^Mdd`!6EP)!iF#X7P?s3A|lTMW9g>a_47R1JI>mtm_rwHlBhF=z1$iM{_4`(cmuErUxy*t8xH7ousu*GThCvi'
    'Pl);!?I!e@L6*>X$=A+DU^k`c|DDvka5j9hRh&hwyQJI{-vj0TWmfadYuLuZxP{EOqCF%2?X#Z|iTL0lMF)h_ntMb4K;<*b==b&aGx#m*yw!p3U;Bi;2fo%e'
    'htE|$27U+U?PFFa{rYRkHW%5AWG_`ui?rt|e-6fL`lR$M-&<oGP3L~e?!ve~#{Z2xFxn-bMY6+Sy@Gu4BiK%G9JLAQ-;{aY8ggVuW1gW+4Zr_;>X$A!w=g!4'
    'JK{TE`)I*7!kFG3=G8%)5_H*K?a|?$ZC3PQV!4rc?uz1xH8@xG4eT9QoBRd$=m_>q>K-K<1ne)6J?FQ<%Cx^#{%$tFe0R@|IoP%-=;-Js;vZ~tCwyo5UeQdi'
    'VRI3S6TmqK`@?S*;jGJe2gr`%dR_<Jx$W)Wu1!7{<))e6KjAyH{Sw%;xQ`r>e`tbSsyd_-@i)~z_@iV$OI?kAwCO{C>1QVPnvjcPuYE|r#?*xqnD5273O?Zm'
    'ZIBb}_XuTNV<+u1vZXmroi8438?>|zc9=s&`6=S)o=f8ogzU2O5SWKsuMrm!pifh)ZsA#15ns`3u#++Q<|5|5^PcmLA^XCo8_3!7g!V7WZs{Y@yI<fe!y3CF'
    'M;!-r&K_(>%rAu^-YMfvxZi0H{?t~C`9}g9K=WljPuRpy%0~F<I!?A8=KER38FQdyUzEdrBh~`tGSBN$`5V^UiZM4A=zD_yx`Vmc3412}97w%o&A;9&KKk0M'
    '?@ZY(eVt@Jo8lc($8F;AYIV+ZPCP)lNH&+B_wv(mA8iPke>P$OB=60?m2ZqX6Xd&yC45guHoznEw$&p%5A6^1*WM>%w2MLCQBE0S1>oOoVGm;t6aQQ3Hqm~9'
    'xpM@52W1V#R%ZDCZ0-QgMGocFO~<bLR(@0D_;G9^2mbjn8TW58-!AepV85<&t&H5ZTr-rR5}SZ|v=+!0JUcM=OC0w+PkQFiC|BG@wfj7%c-@}oj{9D7I2gGv'
    'jT**SC1xQRlWZ6<$(Wy=o^RXe^@Dwha`ZGAxKif9ymX|@e`kGF1z&#@-g%!3+7DBZVK^SNL1N#t4Fcbx&Hx#Wv4#WawD*aS&vUZtqBY0=nlQG-VYc-Uhjk9F'
    'C_ADY>~pXeoEzc1%1xK%1?H0fW_={F8Fu`-dYI=&E04bOs%)FC!jXEW%lAN;TXW$|d)I}qQRmkZ?6)Gv$AxY12Ko*@--6Bg1y-zah{xDt^OCTuS_9gvY1c|@'
    'Q9O3ezQdq2Cfv@5{R6nZs>e|}U>+N7ewFu<eip1T>?4?iuk}aTK4$E~2=Pd3I4kQ(q3^)?{MP*U^%-7)d<7e;xPx)#X4lf1@Jd^pTg*?DhJ#9l_f_7#P7xag'
    'XA^BA7{?WStOLeH?u=9IKZ@V)K*n61_#U*+4#*Oe-@&%}E^rP47bSBOcn|b7Pi`wE&mwSdH?$AOjvZ&wHkI{^#}xl7>-;|8@4!Yrf}E`kaBWfVY=bq<r6C*n'
    'EW`eoqwRlf*tuEqDUC639?7_ucT4dz)n3LN1<c_V>=z}MM7-@L<v`*+b#6z-4G(mVH9n1TY36TG&yvmpJvd-_XFeNjL76)Y{hwkFl5-i~7x*mjt4UWV`$g_G'
    'Wdng8kDqO6pPKtp%m?VABAzi^qrJQb#?z;m>tq?Q4)P1w=L^n_i);9tJ*hm<PtVzuVh9eb5%`$fIq1${jX6(@V^2`WQ67|@4>DKkP?U?xuS&=?n3JNAWfU7k'
    '`%C2`uk0I$yosC}u+5{KpQjiE$~=@$bT5v>E1V$yhUB_>|DVe~!aBfpXL`m}=Q#qMi#67$Pir~O^ZUwuO>+0nxO{Vs5eo^n()`MMmHw`G;Xdm_Q12W}8Q(JO'
    'Bi8eH9pOB1jU2=nV2%*a42})<|IcNs8gDU=u>!DTi|mgf_DkB3BRa1^9kF+`_t7VkYn}PtF`2oQ`y|ayk+J}69EIskkuO<m>dm$%n6t$hY&&hgD1S=lisQ02'
    'w)fCKJaDdhZTNp|BjP-xjG=v87Ww^aN<JEGHSKSx^^85s^e>OXzdWSpsMFW6Hz=87lxM0yISTLx1KdjrpGe~XJG94hjrkB`Z>u%3&#I8+a!rp(J;XLL=mjsz'
    '&|_)xY-Z-aFwOz{>V{ZZtrvLh(3e-^0UyTLYZ>x_WZ>lodnZdCtY=9%r^am08|J<!zaubEBG*)Y`VbgUd#`ZL40G9tBROPZ?KCcga$1Dr16v4n5BLVlMeVon'
    '*NSLk6UN5E+8MHhb6L@iZ4Y?P5B8ZUFYrDrAC#WpHN$*PKEgehn}WQ+I3w!qIbxv6$N%!|+3{K7yC{qg&GF_Zc90<l@VAUvgE4&s%7<}4f5K;B!pCKMj`H8#'
    'Bj(6K|L@=Y?g48+hTvyTZ*u;?FN_HLbej_{*(u<2IY$5Kz?{HZfL%SV!rpsW^~Mj<KDjRZ66G^N2dr`4Rm=nZtUCBzd@bA;=Men|t}o%eLGkFNpU2o=f9YRA'
    '4~##zU?;?5(zJF9id%YD5u3Jge|5w@P0n+PDP})?)xJK3lbr|qr57S5wZS$3$ZOcw;MYy-c(yV2StRUpBjuj720OIZBaTDaG3l1jAr!~-9jx5}`!aHuE&lzT'
    'aq2xk8=p2)zp<A&-}%kqcoF4lK(18XkE!^~K40*xeD^wqb-+FRI-vM5*DF}hx=^ll=(x|LjaTx@rQcYG)84qtzPElJ8h?9E^L4oS4BF+V%j`C}bZm@90oH0y'
    '*KUK2$eV6pf4?K94$le2t*_abc+T@+jIMu7!%j|jAfv+KW-)dy7;lsDevZF;#~>_P`+;NV{5`%So<+jZaBQFA?IL!M7{?sx?%MhTbT{K`v#<$PY@2i5={bbR'
    'g>^#Pc{7XUFAZ!Ku0>+6klc4b2GV<7R;}pwz5N2R{1fEW(W=Qo{KI^U<LXnLfy7)I=LgOs)khg|D-~y#c##y$6XEZBaL#XNw-Xi^>?8G@ddHNRuz$N44_$+u'
    'ZQ)Kq4^d4T+Whh!h)JC7y68`%4|2vhn6$;Hc8jh%EeX4|1-o9>hE?hFA?~w4#SHlxjPoPTYQ%pFtfZ<nq36C*jtzc0C}O;GY53dr^;&Ncc8hISx`&Dl$QXKy'
    '{mNgBO}En@RbLRFwv5^F0*fbO5*X__VgB`%a11)%WyN0QzQ@?>j_~1EkOMGQ%om|dhU+N?-OQo>O~I`n+S}^QFTmR8qlfH$-jw?zeA#imhxq%@E^FWOWvhHx'
    '>w6g^f#0Ua*E3??MxN?m<~`KBf=v|$g(lqR$+-7M&i@Y9%fULIw#otO`zXJLa)NY?j^U+TXU69w_U+!d=OE*Qs=Hpq*Ub73-aRe1xU*v5DrTjAll5Gx5vX2O'
    'JSRCP7u*jMiH!;yM~uxZ&A1Wbk(CWcGH_|;A6tWpt1vn7+!UW1&{tI_@I9=DlKXg1_jQgT{}=Z%yocWBsWQ=&g*v~&Hc-qDhJ8Y{VmZPf$yhP^j%Y({tB9EZ'
    '|0Ncyj9lY)=%1Vx8MbK(Hdmd=!|Tv7_os?e`bmtk@w^l}Pt8XkbZ7N~Iyxw?)o<)PR>F=mW!-t^dm{f#@(|7&%S#^Dr_2`~6LS(e>Un50Vx{tNJ<S&%u?jiA'
    'T;_{Y_xG>mTW*>wJmepj#fRc-_U7h)%?icG;v-z~_AoCj3i&tZaqhT<X<?T8Rd~!53WfRNU;i6!XO=rJ{tlz)_($c=Jq0Bje`5NBVJ{5h1NXjvosK_`7`_H~'
    '2VSp;?jwd)FH&7m$y5#1SvP8r-NFn*5EU#*C~c?y8n+;*rhi)QjOB1#(qNP{bNLB81ItUy&lmLwOMbpv8V#nj=a}Wq;96kH=V+Es<G8H94H%yJ`J4G%-U3t('
    'U|f@%)~WObT_W>ZgsT{TQ|YCgGozfiq{eBK087{`9P`iI<mV;VqLdqs3W{<t_t2a~R~ba#2ZeLhGuh2Tb4MIecs3jp2VpkzV=PTbrHx>|E_GQsSXH^|u<v0w'
    '{7Izvdnbn9I9#%VWf}PFZ4sfk_IEo>>GVGqh0Y0GKu+y61YEUr<Rdtu)G(C?p^C13VhIko;)myf=k~o(wKEt6O&G_BQe)4U`T`f#Czl!b`13%9>A<zZa1py-'
    'B5k;h9%%m#kKi8uUJ(QZ-Tq3qi6uSrx_}d*y5n%l8P=%)>-u-2+U@iP1fl*2;7mwphMxyhdmDjCSv=vHYL!!9#I;lO#%Y9Kn5y$CD|*jS1>U3LO*~F}4d3Wo'
    'Is+;Z#5s$)!N}=XhaQ!H(r>(6^wO_H11_oPz7D-^uRrSWcNMQY;(LvT-jn#u>51!;q4CZ7bvWWV_q=X(6qN?U9F~FcXFab3zpajXTvmnU1*Ku7+8b5+@D3Gz'
    '2PYchbNkh1)7$Xx;F^~{uWhI2xbT}xDAlZyTKy%|s8J4PmSJAZ&kr+({(vkD4s$GL-9|W9H=J({MXA!wzXLzI#h#}jgOx?otGds_T(LG7EtbQLI~>Eb^+!3k'
    '@s96Qst>&WvX^sT`kSck=N9nZ&C>AM!RLc37o0KmC$EEd9}Gv)OK<c^Fy5h88odr0m(=-Yb=@0#{RTY00{<U@JK2PLl;N`~JhRdt=0Yk@s+Iz;0_%eiC0d&<'
    'TsNXIW|g$7bbZwo<xpV$-hNstUHUL69O&;Bmg6I&vK~YlR=^>I*8%@Vg~Wt;=I0sChh{;`2sfxRI4DwTUBU;0`X*jh0cR~Dvx3WP(f#1Cg#5e>rW%%O$5RqB'
    'Dybls@F-JKSZ*<8bC6R`=qiYEyTo^F_UPwOSPf)bxDwxkd%qK<_XI12`Mj?6cqx=3WH8FaFdmM{3TXw0w>r8)qZOq!37&yrtq#kpG44M!U8vkn$+vz!!cZ6J'
    'nJVt_wNCG1Nhq4dzbW;ZHir!h(pm!D(_f>0ng}Sgm=|$B?}%rO_SF?3>AIYmO{`-Kf>8S4P*_9l6S_7u*DW4SPM_OZo>PJ%<hc~4{0(UWV&8GPzUFc&Gqg%`'
    '86v5_Bl`e8Ka;7Sj~;`;(|<OedzY8t<OiWhy>3)WjHRN&rKYtp=D|+M%2b=MzbyE330#>um21oQlcl$Uo<YEP;Xb#VE;~0$L{sONO3I6@<LX@sSE0KI|HTj+'
    'mR5^B{)wS090SY^rY8GXnk~wZDFpxdgAja8+LTyQG}}^|kwXV;bE<cgsi_6Qe&S(xV~>MOXo<buzT#XhW-rf&MXIb)sq3db*jl*>!HFuWgoM&Ldask5(xDGb'
    'Um0O`<M&i%jio1IUtxJX-V<b)`J616d3+xmXHSJbc`eH3cXWO!Wdb$_u31RwLdR9*^Y(k2E>_yTzPvX<5B)+vdaL)W66DxiQ#fDCt<8VS`N2>sxWl-A4j5uH'
    '<fQQ)jXR}zUyp(1senIW7!P5W1Ad;AxyEyG9ckY2a}FUG&lmOz?1wXzkyYK;XRGvUtH-dPi$;D9Y`l^4I*i=Pz-tJoJcXf@3{TnvcIk{r$t`6`P7CghlpV4}'
    '9(2n|mN9{yd_sE})8c{#1!eUK_qi&`G|pkmhC1W*WG}aU3LA!05>#)nEppu?s8iU!u&7h0w1}_2#6m26EZ>fmQuV9ers#o<N%st}L?V7oRBDbYBMP}M-ksR|'
    '3!e+iM4odu4Clj=JPF(LVZr6;+GibC&scIeSN6Z$H{MeHCKh7W;TrI!Hz50bS}SKxlmefJVC%iVjk`yXn{`1WaMQkgbk=FNh)SKNe!y@@cn5;~)DTA7rPRI&'
    'W7K=pPWkB18`@6b(D&ZY6D@(ZebT52V9&69<wzPVcQS5bTGEj|oZL4I8AtjVwu3UL2J93m!^v!WO4tm4c9t3D3Gz!oU=WH8db8$HSdDamA^#28ZNqm`?Mgkb'
    '^6V#I5%%0BY^MKL&+(t?bu$fH@9rmbO$4?aJu^?G{9nvD*Z}Z7!cV=Mpnns-nFl^9_`wd!#AdbU^j}Ats7j^rF-oS!hWtH}oo3kz$yR3pzdJ3xYO~t6OK7DJ'
    '*d31C7eCi;j*=y=7*5WE%yc;HdZKjMOTtgZ5c--^X-S$Pn1|*8))n?s*EQib(g&9QvVga$`7r$7$GDxRegpGw(UQeV(XnK6>Nhc+c$pQR_VSsc9NMOriR)2m'
    '!x-;6Mi|Q2TNrS-R(k1dsW=^b$KXlQNJKC{pYhU$%z402|6&_`Ed0)y!plfQla?y|=3EHd2<BAAavtEEyO&lC-v{;s?~4=)AP@c3$4vX~i@8xTB*SjBOLl~h'
    'jua=)393Sdq7W@pw>TS%)F;~`R8rrnVJf$pBmFDn7NwQSqO=Np9-;~IC=~#E3)9dHTLnG1YKBjjH}HjoPE=zB*>VlisRR?{L*ls^epV&lDDPLD;nTHSe)_Ay'
    'bbsH2xT!b-r+5)c|2B~6-X50DzJu+)LFhBuZ8ppT=BMDD4TzgTdr0w5@IBpiQ18fjV;eZ%EaLoVnj-@a>vzGY?B*r}Gg^!LT|{Zt=HK#81~mVy=?5E!IHgJ2'
    '{sP%rRBMOt6)P3fw1%ASmTM?t&^9FBanaU>{nEV|G3n(LQ;u8M=i*#XuzWVt_V|r4>s-5l5FYp3P<w{qB#Zh6eP8sC-a-FuTD2xYXy_hRX_1U0nW<?3LGk-K'
    'g_%-fDg9XXcT;|Y&v7`-#Xe%J48!>Tr*?@)sX(&XBJ<e_`o8PPxo5uv?9S;pz*1I|8f%TEhj6A~%_zP+lTcLuN^vzo<p6dj$Q?g_z_c^k_<ON7=<kufo?l&+'
    '_i1nO3>1$X(^q3gTb}ndrgGK0V;UFFeHG~i;habBs@%EW#nQ0bwB`~Oh#@<|3C@qQfljx!vZF_5hPYTzWBdNMZ^3}IZ6bV0wLi-#B@w&_5R!xT_KoAtY7AHg'
    'vf(l$wnfI6dcU%cg69<kr1Un!)MbAC!R45G{t*U&aFkhL&hSz!ch=BSF2nK2=4YA)QLfNgWtGG+v>KnCx?icJkM|#@Wzo+PaEX-X>-1<#P)+DKZ6mXEo$u2x'
    '&Y8B-97mqyA)T=e$u0EX<=$jF8D*`rek-r7xy2Iez~8vDTJ<#9_SZDd+n(N&-vjh1l}uqd0L`hrHms9ZZ-tzK(ajJJBI3w)ir@H7lq~4qAyjrvXG6Xh`Tl-W'
    '&eK|zg}`}`-+@f}I~YG;`WyAI_4~=LxyG_Y34LUl>&-Mpn_X_HVm3CkX3y~$qLH)Y63mbBU2`EyE7UtG*hh`Z*ATjrcn|YCzqe0|Sz)g7z`9A!qZ1(wqR2ZU'
    'Wku?}!Tv!iNrHe%s4r5DA}q&%X@G8Na+!ru8bFjK-yToVnT?xRiplfyEXs$>wclBkl5(s|rGrOI3kPTHoS^E|&$iZ=@`+U!>1%UXUNe*HL$)Q^lF4xxd(h82'
    'Cd3(Jq?emr8!5JXL*{3!=NoV>@(+>LA;srQ_@Cb_t4PRG-t!d8krbXdClnJDe^cB9=@S>EBS5o(e6ve15pErAF*mJYx(uNSI9?|0A5&*>?tc*1Sr3-K-ct9u'
    'FK6*Ac6jW3-*L}?@C&&aKeN1l7*dkHH{$oAwKDHz>Q+AIvGH<V;F|h;`CXy^G0l5tI8UkjWDfU(>o-aU<*P{7$)&vCl_AM_%wY^ny@!DIdEV0>>?OW8Jcr(g'
    'WmXsAuLq{i`QE<Kwk|?#tGP*jwU==b4fTBb3*(EJZ(P(yD&I)ZPL9z$YFc58fg=sQ@Na4oQ;~o`%4;bfV8m#oFQ&dXq5CWs6i&k^ArY~hc+SzxSL(5R&#yq6'
    'yLJ)qD5Uk$WhGd0iEw`07eup#Ef7TB4Y4F5KcnWuj5TVTJK)k2xZk8e@|65^Jx;bW!N&byI`Fj5-3|B_$649AO$tp3+fT$cCTK&#-c5-`n9o;`@k|P^SUIB='
    'L%loqX34`w=rH5ANqK9I>-#y8s6>7WQi2f8tX#u<7a@*dE0AtHO^MbE)``w(Gm>L5%M?{!;Jfp#=_;3SCJQ>oJxZJ@;rG+O6Vx^$?n$sdqRt)By3pSgWA;FP'
    'uiVCteZcmZacgbFKj1vSJ4^k(>-ageTS^$`TC8(CKV@KcOl(4o!l1loEFJjEuO63dC8^hqc!8mxI96ch-edmaS3YR+`e;Tmzu#yJsZuS8!@|AS5@W@fKu}JU'
    'DeuY8QZxb0YH!pJ8sC%~BiM6T3Xdh3I?ijzUCKvz=Xt@qPz<1Elrh%5OG=rc-jiV02Xz^b!Fd#WMd??xQ~C}=;EPMS??BH<pG@yp@-4O8x9^?6tMEQDd?T`l'
    'B9m?fW1*BQCwlV?xz5zhS@sp5N2W)&^2}<SsruS{F2_C-xq5MK9dT}RVG1Tf#J_(WcfxX+^_<p`V)YDV&qpNNnuX44UL(p1;UAa87)qwhZpG>86MFA19*Ew>'
    '-p6rUN9<=a_abc3lk~q5dF!9rCB{B)=lS{Iw;bk(qeZ_UH0+YQ<z*~K6{hhUaZ>{$w-halnDWV#FS6Z2-8?4`clUR@ocIoNq?^UK%am*iH)>GMsCXU4Gfp#@'
    '&$iu8!0;zjJ|~oAYW#UKg0q{$_2l*%Ngu?_eQ3z|)BcO+AI-gl^e%`Wkk3h>nZ}sBh?{?M=~%rZpzW&c*E~l=Q6<h7(?VP4UEwqs`@khKe}sGB+;R0V+jR)L'
    'YTz6wCroEZ(ru{tpGWvEiUU{O0_-{PFT`FJRDl`$o+<iF8gI+4xyTkDM@e~4@h!q;E!MNzQBkeKZ;vyy-4VA@=(w7~*Tr}Uz9;7L|3aI@FW_%aOYwOuPf9i<'
    '(??U8`0+0_hVp@_*_~WU*#PFja)zC5+h=XjCL*~e>2$7&`jGQ6vSjf7p0Vsh8c$lTNK!i1@Lh%==lguNSq^7XOLkU>J4S3b%3l}<oYCmLQ8^5;O_pkxgEXJJ'
    '9@+6YemHM=g&ANPXU5Fn9?ps>UQ2{MZWm~e6SY-4Pq}k!FGJ<~^Fk?-vc@h6Mq7aP2KpIm#5*Ha1m#-R^LWokXO$<6<?hru{4uRZ#<^k)BCS(f=gfYg42AOs'
    'XCjE00^Z8MoY3C@J%{B_hfM!aVr~t2g>r8ZIzpDHRs^UMPZS>-S97o9YDn^gXn8pAPkE8Cl*Nw3VWqZ#NX5GeYFT0HXXg2ZuPv{0`8ur+zLnF6G#-WGPauED'
    '57~J)%>UcAn4nscm{m#XZ~4@z_)jVC55ms1=3KR?`F{>aA@{>i?X4JYOyD5x(ouywGw~AHm|x2m7Zf(&i*wF*PxDdtZrBbaZXbf(b0jQ7#$IL}PPl$tixXat'
    'bKKfWnTu=8bB_6Z&L6>^7%vX9<a{F4NRIS1=I;}&jlhnA_eQ;nZ3_r12KyA&?BM@|+Ca}W*lOrUx|apf1jupe7&FC{z@M4=RR`CGX+KLLVOQ$v{mr~oroZ)S'
    '`djY_J0xb`sunGhT4WRWlauiIMVmimv)w?CoDnxNyEYepgw>Iy$}%63k#j^!G_K1*JZ2YkOcCpLK4LiKCFA!am8*sGD%#q}_q1`%q9pr9c3@%7c}v)1D)(}a'
    'vK#D?VEnns-ls-OAKHJY`&3IetkHVM5@9$K{F%Q`$X0GOwn}NarF3|l&V7xwN<md(F1up?5mj82rQcD<30vqaV<v)zoMHl<_smvONy=#c|9hj_-(xGSg<rae'
    '7H2DO+;l*PJB{bYwf+8-lbCDFOOkH8mR`Kk#iCbJ^zF(0wI%FMo$}2~ZvCd@66v@J*Z$yMh}yfVgEl+mE1sP#)8o?jgcD^daQd6*ue8i|u`S*asajyaAy$uQ'
    '&>2Hw<I$6}X1qt(W)=J8mOlTCr{HT|GVBz#Mcl&6>JB|=U12K>I#kTD-lzT+`~TOnm(s2x6>}xVl=q5oewIwX-)D@ccn_+@K@0`S<5k!wH?lD3D2p)PDyZ?y'
    'v%~piqTARJZU^sqqxO^K)vgiF4`0m3m$sR?-&jX$y2T068c56z${!Ey_z+|^>?iPrkS-VW;G(uh`At5ONvD~0Za|ODaPA04U8D9-#dwT4eXR|-rOT6X{p|8>'
    'e2=<~#W3_Ttv6xVq^!K9wk+CFjV{qX>DZW=WB)SOm)_D3Ddor)V9GJdUnKl&X-~;Gm;D>0YsT@cZ&~aA4P8t%P3-5cVZU*H*6?}lXPRx=Qd$JSJuV{Vy}Flg'
    '7CwbFx0Y=zumd{Bwx)U;@%v;OJE=dh4t0k%Z|8&Z=k*S@(98=eV*6bPm!a)ANllEg@cGrH+}r&68_w+UoA>~#@zPih)i#do(&(*D{+gsX!~G}ok*Z~!xK{ps'
    'U+3JlE3h>h#Rkd7H_x}WH7GtT^TWv)S~+o+OnGYFKT+Rg)MThwxwyw4#bk{88M;c_i}qgn1`aQ8$PJr<MpC^9{4&_o;X&$IC1+XoKJAS<KCJyme}OSmTWnjW'
    '+A*&i+@2TpCf2OGM!N@Ub3<*W$JJAeQb)F6F3b^QJczdeK86`>0^~d3ck<tuPx%X}p=P!IZ8<R7Q1nutxt)=3V(WCmD(}Ew-*)zL%<V13rOq>~N3&?WT(uv2'
    '+_v$oTb^M31;#Vrc{`z3m<}IgQ9$)YzKC}qHU2_TPh<QGWYpFx;I*;_Xo`L}=BT-)u8?geg%@K!2v5bf{D<+Iu)H$jcvZv_6cIZksj-e`3_4j$!Z{G41lt-p'
    '8IVN=RxssS!q{_cS;wT2eh=$VZVS6+j(@X@=T4Y*iE6C~3yAx%xrnVPQ!|NLQT=yH4eDxdQJx9<BeGxBT$B&kYBf#)YQ95aF6{B7$NO}yQuq=~p~W@L<)^<e'
    '?qIiX$!xXrj6QEL?kjE%AyeqA2|NEWResB{@6uw~=r_wa$V$dP1bt{?PNk20=K3zMn`NC!c3aw|mapdhE`koOdc<0;3E5(Z^F=!DD(KkgNOcQ?cxxR;w0|iI'
    'Iap-7z4wK!AZxHHO0T>w^2A%RHT+g%93+Qz>+o%aHG1)}9VBK%%zswgz@i=_UA#p(#OsoJ<{}qyP3VgtzXtg5LB#nl;lE((b<s*3#&<w|4(7o~+6gb>GnwB2'
    'I{^M2?LdDCJ7N<bA6@5TUEjE;=+UH(P04#m-OhOm-s2QE8eg>?@RPWTksA)V^*CWlRooA0fr(!xbd21wj&oRXC!O!gnA^`=+c)D+*z%Wbvs*02#BDvs!L6O='
    '@7q^uXDr__E2fu5N2a(V*xv*t;~g*lz>MFBmR(#QVy%&Ep-wHcY21pwQH}-94Xt0YUQEps*=2^@u3EMr$8YH#Y3pj?xc;oB3$ckF#ey?1>S3D9_Y3AZU4=5<'
    '$no#019pw_^U+_Q<VPpAZ5*c296I;bkZg#TklR$_JAR{Wh<yg%4|n&7xi<})&6LqcL)M<;T60@US`U}bcR6-$>)ui6DUjg{@Mpp1NLhc0U;S~Ny+M<#?buPZ'
    '5sPC5YiyZCyc$B^seG*9^_k_Vg+W}7Y$rao1XFpiw7M%nIR@`n%&bShGp_<Oig`uv+8*<GWDAxy*2#i>gS2-rhnMNN=f-F)Q*v+a4P*+@Nw%Il1@Lz;*Y!#*'
    'oa+6(z^5DB8hz9yU{6xJ99sv~q<sjwp6b!p7%M%e(^`TLy7O@CSJkFhV5QSq4pAFVeE%A@?PT4-m{+W8q?(H@wGx6cF2bgpx$ntuB^&*hYBwIU*({v5Pwi_M'
    'ckQ{9%Rqfjey^q#m2yR1$G;t9@!Uupx1t2K<yfrk7`4LMVtw`JlJW}lo-pV2((NVu{Q>j5rdo|b@i5zQJDPT0;Kgng<QFn+NaBN{TC<&^dOcDdDzya-Fjsn1'
    '=j`3#7BsQ?L*~}%9{fDf@EbIn&FaYWnxl<Z@pFgQUevMeL*;X8yEk>-S(mU6SLV74k`a_oMqDh$Lxqls-6s~?h@VruKhrDg*hrcWEb%Jz{?;Bwdgo`tMSp*c'
    '&y7?)`gY}5Zvgh>!f~<fGw;o<psj^xQSOc?Jryk?wvqa5Q>!>VXD|G2N9Frc8$6Gw?f+r>D1(Z!r+CN&#j|V$cUz3RWZSbXS9L)C+HFL_QYu&3GW(qTHOn`t'
    '9o@J9>q?j}d?!(RYP5cVd*j@x&oreiw)d_iT9;Yv#6r=M82sdgFIx;^4XF3d{!7#Sx-C|setFRMsvg#0_p5Gem}?Fj^$V`|`*h$K)b&l5+Syt5kyX2PhvGLY'
    '@9dq;xF4+1X+hMl%;zRrTM)M1paX{Uf;jd>{6*)~Zrql+U5@3#@8DfU&W!Q>1y$20>mp6sU74dLpG44sidqKE)rWrNY#im{JXl0k%r?i7a(EOA&g6Emuu%#-'
    'wZ+_z{hi<~FzcTF4BH52RN??zHZCoVwmt=~$gNM1Zj^n{;(=m)%&~L$CWh}N@aIN6rgT~mRu$I->t=~!v?%HgHa6~w_H>LRlXZ-N+tmC7j5Xx8Yh%zyJK}z%'
    'I%kks58^zT@wyN6Y3HFMFofXS?*#9nz$uD*X3~R;Jz>Oap6=r*Rb4I4Wh7cXWwjh-dCTLqxfJZZK=D3)m&ldx0@pJ>liPx7>@v!1owLr6gIRcA*(Uw&enMF+'
    'a_`h?k=ttzM#Z{l(RruEX(V|UINs^GG;b{v|A{#g_zvljleoy@X)0<3Bre&?4I@=<xCXyXoV#+9?j`-73G&_ylD-kYv-BM!gBOQmGorPEZgoB`=3l)ex6nPE'
    'h^@A{t#z!`b|vcd2bb(t+~WH<Z;otrXkJV9SB!6n@lAxWImWq+OnefpD_l=`gsY=+`f@)=&c6G4-=1$Bml3=~Y90SSG{>q<9oKb>TELWCJl3yGP6ErQFc75k'
    '#?%URy?A!k6U-gdJtMY)d)1McCc*s_&%uLS^q)``SZC3|#(ZG=BE-}M<*zVk*88}PJ7@iyEe;<L!!aR@l=g#_46H?uvb`79!pS-=Q9qL4S<J**rQ%NzSBTuT'
    '(L2Vh0**;FKps`n;(zeFgHh|A$$8SkoG)S&LvDY}{IG&M0eo?oAGUp@z6x>u)o&tt4$B|q%k~h@f<8@cbjJ2jWXmEQ{MUB$9&Jgi)u$TOeD?~z3GJ_d@xOF-'
    'u`L+pJh>f6_1Ff#bOyc=oqKAHb7_5Lqcd$g>KNFg<s#y2J{F8GIinv(I6WK>o*l5UKyMs5H#8znnOR}Zw*2_BF!3YIGk&A}E44aC9l8hI_xIeP{JX(rru6xF'
    'c6IogYvT~d)1`4~QOomfw8Qm_t@9zpUa;=p-a3B^TS&}Xn%wMDJ8H6fWe(4C!#BeDti#5(2|DI;({?`3zT;lr#@#i_Ip6vHVdmvpF+N0YgV#GR>TAj?aGQB='
    'Dfnaeld!3Dq&{=r+`wWO=Y-x3ZN&r9z2p64y`I9hZ>>Br+GP^+DLHGFG3=|dMTr~9Z}R<;v2cl_XZ}XK*TBxJDC~2bTPH0(E;z^AcpmB-&`Wa6msR%rQrjqT'
    '9iM0U-l=<L+-LtS)a#S6w@&Ccq~##m;~f2WVCF1UO`&LGtT3bw_)bNREf8<WH#p%G?jRkfEaY37j#Bvtk{=@9ku6^>9O^o#C37sc?X<b~Y{r6qm9yD83NQHe'
    '_)_IR$D3s?zz4Z6?%&SwyootQ42baYyjwdj^BV3+;NZmisQf?%R#am(@4R~)f2ZaWYy@mm6)i-}enK_7@|kjerT<Cy!Z8@KL;0p3WPk0z-Yyq&;NN7(Bujru'
    'J_N=Afn7AK_YFBB-$gzH*QdG(q-||J%RVc*W=L?HPW|-vo$-7$(cclg^0Lm&;F?h5p&W#9PR!>d?5{z^EA`8b`+2C;eN}Si;*8kiZOU9B>L(sc`S{HoN9w-d'
    'R(2p07HK&fiaj^({hs5#%9bpCfUUXxu1u0#{lxb=UZ{31M&3;M!2FzSyt-X`A#Jb>JNAxT&CFlfc}MFEq}>Pn_1xskHDnp{ot&_UcS}6SuU2?aI+*l<#9V<-'
    'EdQMnPvsmM$~?3YBlS7S9L^u6*lCjUr6BSPHU69COSH<?ZB>vvvD@^Ws@EHRHv60Cx##tp!(37M-qbd&s`)$w4u@hQX?1m(ZLKu_a9VpFY2)wTt_B|m<r++$'
    't0xOzDO&37H7*j@UgfeMP1zXdm4V-8bIPR7>*;{)Cowh~Aa==b%C_j#s?9G@Zi8xBWJ@<`M_PO#@STb!;og*T%L5w$_CCLJ6>OK^VT!$Pw4cIwic=kJ7S+BO'
    'hrvJeDDs`8$1(nrx*uXKp?J@x=73V>qe}IKc#XjF;hfGuXPgP2zUx15D=D;pL_21TrC*z{e@~HY(f*d+YAZDOheS?7aR<qmy5b{(??v0eduNFAGSU`H%*S_f'
    '6f#Hn^?d!aoSAReDayEWOrGo9r!8}IPmMaq-d+byjJs9F9DA1hIIe8Vr1{BYtjOv)vo~;SNAZjVrqaZ=8JsVQXReionM<j$Ry_`?uF5x?zg00_!X=cJDz56e'
    'iVM0WaM=kQT%zt?Vazl?T-E(xe3Oa2yNGdZQs2$zI`mE2J0b@k#wK3Gd#1kgzdKK{r86M<Fr_>rUj;vF4RXiGom#CAFNE2L^(^`Y=AeW94fY4ve~P<=d|`!w'
    '&%zwuZ*v&K8-|%}50A5a6(&Dr&ntf-mT$51DuQ<^^ZOW<kMZVaejwNeRM#S|SI-7pz^J#$$oIgwXNATIWD)2h#L!Hnt)Xfkn**$)3j`l=CSEFu4IAj#Rhmsg'
    'en%~dQA(|?BCcrSQMKpRb7?GSOEYikn&(2)3btT=iJNw?5p_OzAHfN1V=>u=bzoloKE^Hf@Gd*U-cNGcYkx7pQ7^cpEuMRPetYbJK3|;(mto+UsIGT_d(Ydl'
    'Zr-e5-ajVW1L6Y2+&2^P=FIpq_8!CfZj2)j8<#KU)R39zJIOYhs%5Cs>#{AO`Ya+G2%l%q)tD6Km{~HH*k~`#T1+=YLO+3R0ryFbv2Cq((T|N3E_8SwjV+RU'
    '4l)1rrnV(()$^O+W<U-Llz&*?5$kcoJmg`b4WagX{Z5vA$cTXoo3TLWfb#KlcFOvkOF5>nI<a_f);Zz28v7h%i~)O4E%o!bc73+%iSbbEV9Bur&2^ZpP2rla'
    '3_MB3`iZ#EKX|r`cT=2S&4UqdiX7L-A=vzxQa20|e7j%|ct`A+p{`+k618UKo*AMQ<W}jV&__KY+mYKC8X0#muJ<@rNUc|~c8vPtV4UI*?4qU6k?T45SZGhS'
    '>a1Vef)Bq1&(gn7@WsfpYSfB!k6`bkeZW}L;XcMC%{)b#FDZ7u;XIh!eAsr^<d}kU6FcXy2f6kb`;t{H9v&RK$@`YUHE41p+Gkes2&!jW=SXm+n%u(4vy|~n'
    '=1Wrd%{&{v(HlzFRb}tz<n<$2H>K8?Z2Muu>B4@_z$TRD1<Zj_PFrKAvDU@J6jD8|Yx8Czmnip4NaN7b{A^Ssu-5tJxUP`ns<{sM1rFl{m~*im@qaaMglw6O'
    'Hjr(@s(N6GuNlwVVb2we6*1oukB4e<1V&;_pCgW;9rgEQGwK+b%YT#Ii?Z)5IoI04*U>gYqKAON*)HFWWsc67I%+OdUIBUGvAwIDgN?+4U|$f%kfi4rsZS@`'
    '6KVB~YCc5kcI3JEo9o+plC%AaeJ?U=5=_3hT~ox(*I^_2yi#69)aYe!3VfqBPvw>opNq9qn&XSthrXL)9t3fc=sVlInxJPF=M3K?QNM;f-P2o6@4qknEZKjE'
    'duf0iXP!qqH``gQx|?%v4=YFBb6_sKe$@wg<vDI|v~i#NUU%r{7K5SV^$?2|uhAK8ypmV(u<zrNduh7<a-g`OIJb(rsoX?tp{UO!94+lfAAGij<K6<|U1WR;'
    ';RAdjpNx4XzryZA#I<?V6ZV@${2JJU*!Ki$RfHWXd&P*Jb-wfaki_0#Tvf%ZPKtL$_h-`cD=`$f9@?gbxdxqtJvJrxmu26wT`F(|W^I^(_e$`6rT1r&y_f1j'
    'PCVcHKD&35RpXy&CqMnw+PW9gIlGS0M^fBt%drnX|3$r=Uc)ozRX#z^R>NR?;WcI4h}=_%K|fH;J<L(vuE>uZCi$cFxt!~GOvCK4E9E-&L;<-YaC9^l!A&XU'
    'P|xkYKT++KtdV~mKIvM~db4QP6-XI*k#cACIxy#kcz~Q4V_C6vwt9||r%F#K9*i=^efmPCCOCtb15jguYVL7XM!y$y8rRIEV3K2fhp$@K#9DxpTu#NqnSD>x'
    'eJFR4T>miE?}q9xVjP!@4<U@S^?qN-G^tY&;|hKt*U{MfEpeX2T%Dx8ZLG5IrSJfPC&|yde@p#o&TA_^26$HM&K}458bhxiXZbnu_$PAys-K)y+qd2X%pELj'
    'Kk6}<at1%w6YN_~=L0z|VT`*5_FdUAmw}88ik>Ty&(7KdcKi_J2lj_JOKea3`edF{&f!&EtPPB2W$?qw4b^J`<QK^J(t+wStUVi1T$k!RXuUahZrs<Za}?JK'
    'TylR&zhjKLI;YEl#gVC>mEeFeY6+gdrSDT}CJnt$V^KBtc+Zu{7YhuY!Ys01Lj6TYEW_T9d~JCxV=5RY61ARYPEBD|kJ6r+-*TbisRR3Ge9e7!hfkLm&AF=M'
    'yVB0o`BBx|6uD05dAp9wc^241`53;NY6!qSAAfGXuvPdT1@FZ9!F^nolB`AiUq|~~+RkIn*@R7G++Y4)zGH&V2+jb;uZ%rv<*Z^hc663!qE%~z{hT|G^PCua'
    'l67&IUQchtqqm5On+widDJKnFy=(kiVbt(!gS;sIH_LhhvoHF^JMMm#{{;EcFmnZxyZK=Dozi(R?xjF6if>^DNp2x`qK`PhSz&P#{p*lJfqI$1{9*3)YI1CU'
    'H=iKl<L`@lbP1^LOvS~qCvg$>-%ZT(-*86H!sd|IpS^r^?pHoLaL(lSw+`WGy&L%Q$bpcP9H6!?Q1?6c1c!Q`a8z@|W$)y8khf+*9CZA=VYT$^Io;6+ZF=ML'
    'eieJ?A^)l#cYD+KhfcH7#(Xa5v^O8l>$TV!RfE^TsP_%ro&Enmd|snBDprOSZ}HW8Bd(P0Pw^r1zU$I()9>_UFNr+LLhsn#a7<=^J`(uiZR%Odzu}%hDfjSX'
    'KN<21kL<6WBG4D0gNo0bkM@WY)Vr?9wiI@^jKM4ZUOP^DS#|2GhtBiUh5iqAUFRxo8U8nXf9bsI?~`_p9+SDpCXSn*<)e*Xc-psz70|_eMpDmDtt;mp&x`Pl'
    '*XmvvGeKQXz2?LjYYj4edW^xMEgzzv)~}LZ!7}XGq1<bdC$L*I<XyNR-9~(B>wxnEd*wTC7O&__|Dc|Bh<ugIufP77p^Xw+y%V?xv9#eZep~aXPg&fLmfUqS'
    '%xBkQeGM}9S4*vr_QQ=lB-hlX%fch}FDCozUDY#@&j)(&fS6{Gi5HP0#J{C}Q@ynf9u)KbR!=UxR&mwCSQ+=r@>Z@N<Gay*e!4WCFRsvut5>Wo9OB;mX&i&O'
    '<=X|mkLro1c<=+kSta>f6;{cv@5s#E%3J{JA&l3uy-YUJ2gkMh==0$|W?OkcIWvI~K|kC2T-pQtne0=Ofqj0vHZP*qFZL|LxxE`X5Y4x_qq)G}&>E*=F2JUO'
    'bA6r_=KGd!a^36k5eM8B_j&K6bneyWETNbu>B7Zw3OXKr_DtVL#kZd>Y0u-hSacS(&EWS7TszmGhs*EPd+C~Yqc^fq|D|||tlAyql~DZ<kjEP9JXdy)kbj4K'
    '`37&U;;?};e^<U(u|>@{MfyS4fh6=s_FD5i+wyRo#RZV!lQlxgg%o>-52~%#a?ivO98s1`3eo$_tf{UTeX!H81O?eANZ}RejDwGYSby=H&yOG<y-yGigE0%z'
    'wZz{<ap3kA$Kimz>&kq%KL6HvAvy53h_xVg3T(Ue3G@K<M|~YuF4(tJ#CY&re`=k!03UgEw=Z}+S7796&3DSkO<q>W*8@64)+MF!FK~`^=p2zVF{o8mqMy24'
    'g9G}0L)hQ&yy7Gex`yyNzmFnMhPa($DwmcW4@AEiWr~Y_JL3Jozoxm4pEdTgiyJuO7vzT(?|jJ8@5)DcezJ06g8SjF{r_EC+{m$kk5OFDL0``bJxkxUeuzWi'
    'x`h(RPVOyl<joHMfKNY#BZP9`W(}?2=916W-1jMcr)MoA7e5_i-^HiDI6v84wEm1=ndTZXqae4|@C--GWyPoeF)u_Xdbit=`Z#ml7xriFCHfj*3{&*3C3(`S'
    '1#nM}p;EjV&p|DU0v*$g<%MajOx`kQUPsPtZ<DYiImXAeadwY~!`#7q{3rW`o|~Ipt$Jm14K9o~)44;@KN#;p@%+}k{tzNwGQL$82~N>6Vp^OJ*>{=qV?D01'
    's0vIc<vXyyb8+bBB7d}rMtvIxyIqWn!fp^_mGf{dwSzJC>@j<txG^4l@;Qkk8*5&OcY$~_VJ{(9QyzPhk0|G2a3$`j=laS%AGj`v+nFKOL)M#{bvuJ(?=LYA'
    'x<^F)n9@H+<6zsq=ohm~>l;D#EyyM>%6Ja0SxOC2;=0rtB%{yGKj72Ed>8X)8r-PVuT9|Q4$K|zF<&yS1wS)P;eWkN>!=4WV7qWFt>}Ff@70l;?;_^(W^P2A'
    'Um5dZY4U;R0?wzXl~T1J_j>(oQaFN4`y;lFY1d00f~{lKj|m>%k8G}@pTtnPp9a+>FjtV~3PC$;wQmUyN2AW&(33`=ZKEz;<Vv~6JDr2-iSU}f&e@B)*s9Kr'
    'R!@t#-Itb6Fl;_!Orp0h#ep5x!H93Sa~jMo0_Pw602`BAZW4zQ*~><ZuydPd@wsK{)%cmahhuq7!?Ccu&cdgpVM7!?N!UPykG#iu*CYS0Npo-379-d6%t!l&'
    ';?`IjhB$`^<)X~n5hsc2+2h6dZ3VYbUA^na+{DO&4NX}3JoX`6N3ag@8b;<x^IVisd*u9XeWS;EJCA&h*lRmNdvrHfc_jQi-Us%(b3ye{7H1dfZtS^2{2JAB'
    'qv8(bJH_Tf)`+h~&eq`i<#kgw0oEp`?PWMOXkX9JmdV=FMop%y;UMm_Oy11P*O=UtdQz(&;ZE9Nao!YJxAvvppgoNCkFcHgvQG0S_SsO>Q<<Eq4X`87<}!4O'
    '>M>$-+1mMqRy~8CG{rA8{BM)XafN;5-(vSw^|n~sNj9qP_hQE$n$9e85#M)=<K`9K#EL)g9um2l)Ea>pFW!&w?kT?$SaSV4k3MHSV629T{D1cQu%<@fv8p5&'
    'hJ2rOYJWA32kDxnl(;Biab<jt^?MWKI@Z}OjGos|p;v8|hEccK9~DOe_s5t(;TI$3%$L{Zs!gvFz4Wh*RQ#RM>+}y~E7*}3cia{wXSwXbV(p<tzAAI_%05L5'
    '&D%iA<D$XGE%0mkCO$7OFhwSZe_D?c##YL{LSOcL<2n10958!^=~`Ou*{tf%b*;cp&in|^1o?Hv9r7C*y=<&`{OS2O=R=%R`Oe>z6W*D8Kh?9Ngtfj~tG5he'
    'uL?5v*7jBI&6epZ9#4M$4P;~id>Tt8q{K4q_laX<1Rt`%RLWdql27uUjihtNiS_!aUG$G+(s%3(Q_iIL3yce<dKU}-Z_2ZbT9gCVyQc4>Kao+RjW--R-k_J0'
    'z0&!&*bga&U-o;WNN_);VSddTgfw2<Z^)?#=i{=%TrBuV1@LiiI#1L;C>{wTzL91hruNb<|FO(U&&z8&MegVC<>vW!`Zt9>_$Chm12jUukNVL0Jg1G@KA-oo'
    '2;w{`9D>Rzn0}o0nMB;$<X|Q4#8ljb%x9<LG@h)SgQ_X2{ucdNs$dI__MI?Vd_!hXd`;sRm`BbSM<(}r9KW67xBuzAe~cfw`&+Pqkt-vO%U8s76wZj|PmUM<'
    'pLNDCMh@$NJnQN2pOG&$`so<BYwAlQ`)@^QeRcjAr&5m9omX8wUVSgE)8c)bzN0Tx{^0vIYobPEu*rxUpsqnatKQz`8p?<d)Df%2IMCSH3IpN|8|gkfy&t}('
    '?f<QVoz~&1Ha5E-1?8~BZz$&`#*-aWtyFI`7wBJZRE%eU{9Y5~yDHvS?#q-fzE8p5lsH0oADDaOY^MB~gR<ZB?c2J}HD-rruY>3}lElBoWWI_m)yel^3}YSZ'
    ')?kfpI<C~hyb##MAP-!D^PspA)eOlTJ8=k$z9$$r;@InR&+Ar4SDM>*^wCkqU{5%V+sYbMac^_q2(BdWC^=ZkAE+_k@`8E2g&n`)zJ^>gWqwY-!CnC^(JMve'
    'ge%?BDCZ3meSwX4X#F%lOmQQaXX1vV-UYgUxjF-Pdc4N!4+P)9QsH1@JcqOG?~|-+fIV|UZj))D!!@X*j=?8}xw#a+KCAxk&wIyW?e3(o*u458$A^8--kl}l'
    '=)qqDnMb{Y31cAiyc?^VsPw(y#T$KgtTBCo**CH3o!is*`l(9UR#1Gad)1d!bM(dX^hRxy?q^Fipzw~^cNXg|uF8%Q90+spVcXY|<X7J7S`>*v75HkIQ>%A0'
    '7bN#pS<XPF$sC=E%OYkF@nBA_rTa-^&iZTa)hXJPW#XC!IkCVPTExDKW8`8Sv2Poa^ZAPB0lBlOuGjCs`+4w>uUXubS$*FX=d0ZN2Vp0%zaY6-^?ZLYC$_^J'
    'H}|nEKj4LV5NDx}d?oL6ZkVGR@z6*3J>r<br(NJV#+<4nV@-r{68*U*gA&%lQym@0^Mj~8b};9IXMZ@XR6?7p)}^{=?6WtQy+~R981jMaFD=7t{E}J=Tz|$O'
    '6cIo10saQ)!n8iO-}8M1Kb@{oVqb^&&r@{Y$D&}yQjhoec;h_iFGHul97W!X<g1+cUCDo5LSFK__*j8mBYO(>z3&~U?_FrdtGuPC@8UiP6|{M}(Q=TqEz^61'
    '>)eN^$5OePmOB4-PZ!x|6W&$NH}y79^J3StE8b!9Zx8JRCr(frj4q+&=X!#>4c`~8VY%SBHuf6*MQsP*-{0v|W!O?8exP~GE&lQ=+5s#tQ+x^Pf7#f^?Go^v'
    '+1^xh6T`F{_fN6^f66P8_1jdw5W(N$>Dp@SE1Xq#ofSW!y7L|8L6FbFyg$|e;MfqSLwvuj6dZCk2aMPsnQ??a;Uy`joM%U1<S<s_xy)}N)-CyYdd&FCn2gaU'
    'l=jIkF*jgK$hv+dGi1-|9@vq?#{2$mzuT{S4(UB$aPwX!?BmIL)uV~^Q}2)UGN|YC_>9c=V_pcHH^EB;`ehwp{ORkt`F_2B^6b5XEm$PGTh?~L`k1i|J4f#5'
    '{k}UK=H*73Y`nbbc(meBmARz(%@KT6CC-6Y-H+)l1xFpj2he@T#G$*F93h%}p}8<PH;!m;&Fd*Wcg!3h>O#r$qtE!B$A4L6Jj38`Na+m<aucr6wfK1&hv0gE'
    'vqFB6Vb8$aGoOg6*?;^7Cq!1wg5q*`p<L_(+VF&Z3dYD8AuwK1j`W7cYV?<~kCBNFWd4BwdEQh{pseRw_aYy%xlHPx>QU>Tk>GT}UX}s*J+f}Omy4Do&npcp'
    'QEf2lmb^;zI^5-2S^cjmhwk$}_sNEZa}+tIoY+uIr>6NQ*dNq;0q#LKDAYM=IgM}kW>sh2<#UBL0@~;T&!&2mrNy{}O(${}N7<*7dA8#@1ocd#R`7lgR;~fW'
    '9z)nG8g1g@@q4_}=Kt4{lZtU#HYe3la?uzz_e#m29b*)&{NSKS9E+fzod7=vd1sEzzTZC@uc3Pc$3i|ahni<dV}TBL`>&_u#P0SVPpiAll)D^Xxc9!X+ERmS'
    '`g=a2<*%meMqB<48!F$A+-`PFZB`7{<ja1F*%-Q>0rtWUbY9phH~v!lC+jT!Xda(JGwz$lbDg1AWAFFx(rI6FbjQ~9D_OxZ!p?b#7>Oh6Vy*dwef{{ocQ$!U'
    'Xx_A+l2sf2|A+ZSjCRdigHL|R#AWDyVj{PXIzH(awAdCoN_*Mhf?^#(x=QN*SifQmTa~TPB|azf{`%UV;ywrZ{mg!YrcYF)K4+9KtYK|gaYwZmqIb1{xzTn&'
    '_ygMsY@cu27l_LnFdu4a{NY-3=2Uw@d~xJAzYc_tK=n3BpE-@YK=Jid1m^_pUy!x3_WXUH`hIQLPXXnH*L+>%Qk~+fkM)_Oy~K;RBX>zy2G3n`f2}IYLzm-R'
    'Vr>T2crR#sV2mEIj|q(Fmpv_v+BDBKFrrSpw}jdQTF1v@8tBWTUS-(~x;>))5_Q2*hU-A_AB@9-pEJh#FT~PE?=7qk80dHv*ethu+zO`5hco2N*r(L`T*Ww5'
    'A0YM*^IRMIRuDd{f$L<Qg?oi!Y7{qJ>iQ4z&y97Iz3p5Xd*NPX&0231bs8bZ<@g%PVQ^1EE1pHTo^ZHvN50eahG9c+NAj33U(9?zPvNLi9gdJM2jfY|q3es<'
    'pl8SId!Jrg_(gn__8H?h68Rtb-YV}yy%-+$DYcqhbKnz?3*v`F4MMR5|8|HIr^@&u@PAPLzhk_eYeJo+pYMN69CxYeTPWw?UE9c|UqzWJ?tymVK=*KN`^4YM'
    'JzVN^mRy;Gnqsc#*)hj51oxtc*o~2l8`b5QitC_;JjTr(=HRIw&*CZKdt+TU**|0MSxdYOHS8nv<Wg^T&Xt@;j@KJuUrtB!{)qWGWB&~FI|Jl;fcqhbo2&`M'
    '`X##0Ef>D>dhciX=pFNDEs8-io>XywNG|UD=f;fBqKx>~`=g#^ZYVWAG51UEHE;D$qMr9{BW|j=o^-yNb7=J2qN?vhoI2$JWA)YYnSt)3h`I>*P_d5SOm&&D'
    'SefW8@k{o~1)C3KLkV$gSRVlI?V{{k!TiE_DW{4ttvuzAjs8n=>~o!COK@VSy02RDom)J2=ad>~8*^8d^QLQL#r!buRh$d%Te?NormyLYw?XcLT)0teJu5CO'
    '@J!4xK=Fn<x`*;CYx0Fuo<-;%KX<rW2e0dFuP8UrU-BK7>_OiVm>GN)^ZPGVj86I4-QpZE;v>Sg@0p{MIQ;&PfBldD_}6hg%@-g4^}qh(U%&Ei`NFSR?w8{f'
    'AKr3rh1{>{Y_|A4%RT%q6d#N8<-(cgemy=;7lmKbw_>4CEV%QzyL2COZ;Ri*{`J4%cH3#VJH73uyZPJyJY3<n|Mjog^!WDpfKO;#D_}czukCyj`<{SYz`og0'
    '4))l&bJ>TDu-VglOWQ`2x2JsC2G<pA!|s7}59duVUaRuz3*@e?tK1vN=EWNKC&-2!*<T`;vG7ZOV@*^Wp4wO)7)yu!hjt$KC~$ronEmP*bJr?wzg6*XEf0DJ'
    'h1;8A%nS3-E8{cNrN-a0LZ@ZMsf~L|U(xuU>cPyv&0MwdIjpl^q8N;EzsITiOmGvT&h^P|Fy7Iqr4>Ar$e+K!e!QIPeWX6Z;vN)(!*>idUXgUglbB1yvSGe+'
    '>=AZO=OK;jH}0#)b(A}ii$Ga*6nKaO*8b^yC;VG*REfE?@Q*saXy7OF)h*kgqP}l%hm+j=E^lGbma2ym@<?jm#Hx#s^OA)VCti{f*GaVPyvfnPHZ3RA%VDJI'
    'VzH)gRzsW%=2pFpUns5L#-AU^=iM^<n+`Qz#HydIVGmy9u*>}?a%zJtn4$d9bw~m~fp#4CLfaFcvM1xLSTCL6S=P2~)!@GMbE{>!^oz$qeb$V+aTyCzJvoJ+'
    '+7~vY#8L}P9NmMS^*7pMvrv65>g5R&hT~Q78lBhWzGuugc@j013agEF+Y;unILTKrr?T{~jYRF@6!#qJT3rixLw<m%zhapSe)iiy_Ek3OVG$c}jrF-_+wd{I'
    'mhG|8cMxo5j)i$P9t>>5EL=gNFPQXCcR9&zIrINv?XrnuB|VkLzIzL*)0^L%+Jv5C>v`sH*KybJ^`L79wh{FPXo<1<ne~Rc9$T(2@ptfjQ~W~jDCEDezqr;H'
    'dk-<6Z~wzOFR6J4S?i1YhW8fskyCZe#OsgmW^fSl-Z*k??n>s8Wgk1R9T1PwByIu1qJnSM3`@xULEp7lwxiuy8;m}yey%$5s?BCE@<x5n9rV3YX%uy<zEgS~'
    'Pz?uh|KvK)Pbu*%s?lK{JI)=-IXPSBMxBom?6aPIUBTO;4JK=F;oKA_)RPhHuzK`?dkfC~hHKPU88LowF6F%l`+&dSNUVb)lR35}If3T|;#k<LGgtFY%p-_C'
    'vKTLar)o8r8$S82`pjp^lubrHMD7E`m}9I}?=6L4lX5fhK8#Nx-&4w^<UAYqvDbn>*Xx*_NAb==Mj2QTz2Ct;zWG$Q9X)7YHZ>ot@pm<wH{gpHyv;Gdz5C$('
    '*H+I%W%KLtD&3Zr+eY8n;PX>`oJ5}{p~pz4J*u9}q-#-L*tJ!7FU?hrebDh-i#!U}MUH!W#hFpo{Si(6*lc@2=0rq}Q~AIO+h+D2G4dudpEf$7uLbXkeWv2S'
    '{iE2ez|)I+QjPoVZS2UJYNMu0KgaT|s3&GjW~#iCti?8ZbAJz`Igoipb8dJJsyoc<!<g4|#8pWjl;R}agXkq;#8H?#hhxOxyX!s?x2b=KRNSc+<$`gJIS7<3'
    '6p!^Dk)!I`EkFI$c&=PJ?>&<jXum-FxLkxaK-`OWn~WET1CZlCAm0qu?!TJjO6R+J9p%(qG-o@`(M|FZ$u6)#E)&GO)n(t43h}BSeurYca_(y0Lx6kFtg{t-'
    'G|UmeeQ`a5FQ9MeZp5+&$SeKq=-z08zZhdMu-9C@cQ)WtcP#FKm(_jVTft8R^G^N*_wG)?e#SUC;<<}7J`U`y3V(NF+#PqgHWGYH>dfgHVb}*a_l#}k+Bp6G'
    'd~R{gV>Qj(%dUPn$oU>`qYK&gRM*=5+l;+)PJ;Uj`vaa+B|lQ+`ZxFPV>b4M>S|H$Bzb!4-F3it1gV3Pc78?<NY@5ReOf$C^Vs8kt!#J^-zT2cdwj)-xzv6>'
    '=*<#h$m6|V4(xX&>%7}r_k#JH#%>*A&l8+q8Oy$_yY%^{+z#1lV>g_JYXdi+Ikgez5ES}*<c0z{v1)}+pPlDME^EF}4ojcbtp7yJgqm}WL%Cm*@<kme?b}+l'
    '$T%m({~PBdP;!K1Cu8-~eYj-*l3y_lsmk+6J_o^Rr{s#NzoPN=mj<537)NS9K-G^$&5zN`C>uj`Av^;0IA@M?iMtLhS*rPNG?r|5lsZx7*BJ+<?<4AVqa9qg'
    '4MOnwblnE`LV<hXds3`aVkN*Iw`wUl#*a1(`p6dFc-;4l>0Iv7P50J3IJ)ni$|;4|s|(IN=1GpE!-@YP4Fh1*c(5&<!J~2)r)%`+pl2}F4(nGWcMjFPbDz(E'
    'cvVnm%-sfJw8Pv3;zH87LR3E%vf)Tqg?xSz{RW@EUpqaZImEbQ|4Q}b;|0b;uBsLYWFxH04s3DIJ7|x)xvAj1F#CQcao(c0hp2U>wW_)o>U)dz9Ml(t{VC)s'
    'rt?AfTg7+~V{vj_d!OtNYWFnKI8(+xUR$v-aUBZzaq(OuVL}nh&bn`XZA8UcZ{SQP1Lx5n9%gOlbM8~$M};?KKUn{L794vw#?ykntHN)}jYAqAtIUTrw#66+'
    'KG*MM-LrlD;(j~!cjP_QJaR6@^gCq!f4Lw&EaE4Tz0V$PKZ0wm7F0Lq%`iSQhP9XzJ`&Dn{!`T%>-&-pz9E*@$VX5=5UL~A_R>;d>X!a>lg=$r@6I}kXBh1!'
    'umi_k#AKX<tL+1)$qg$8w<COz$xXt(d>#Fvz=3OwJ#f6Vhtaw{4i;~>-eox;&Uf?K?B^x;4kgY3bAWK}jrVSEDR!fBU^CP!<Ikf!f2sb`mU0F0Yh`ZQn3st5'
    '3%rB0C9}ApGUZl6cJ8<~Uhq3c%$X1Ge#<G|`joRI?yq~KiC(F)&yRXXDcgR)S=^NKKsDy*7w$HHCbtaSrjdsy+e_f0#J@pqsTx%APTWs~dtR2~?~8i?_B8lR'
    'BIZxmwYU!t-P7h%<UXAuS5LAX-!IOkN4kOh>`aXM^RVKTh8wTa2sxHwU2oNuH`4i#YEd*1m<G}3MPP{S--3;F);$kU)^Sb{<&)9R@*^Feqhy5cu`6u;xc#rj'
    'Lvr!EXOw=gxGk|aAIQ)UbaBqa#hV;RR{dN-VgMXGYs5QdpEaz>^$r1_SE)lI#s{xI;Cd-X55mX8c-t?@U9PzMRZk@u7b@!f_cPB$Fb11ToDEq!D`cz4)5<&u'
    '$QZwwn_&ImH|-9=k#7FZ!1)M#N&O__`;wE3eUkKfL>`vE=U1J#7PW1Lj1t^m1&)sj4CzhDVV*C-kWa^pSihb7`6cEi_m&v1Myx{c6!*6iykmR$8@AWd{OR<1'
    'B{TM5-=oMqu)uDcU>`{K+mMfNJsaMw9l@SxAZv4HtbLkZSDzp&k+)LUp;1h9bQ0VRE1Fx(FJ6f!Act`dkbmJD=8o$J*pI)@_xvi!<(}9}a=e40?3I@iXO}&a'
    'p7ukhE9}cl%-+nzhVovb9K*>?<xVND$u>9kytId{ypkzjIWHk=zd)ud?sjcEi+7gyA};bQA6C@frruMox#&SJ^_qAuDW4UuV<wh+F)nf(K2m2U8+XXH>Y2F9'
    'xZR=Wt{;=%P1V0W#-ELS-mi-q)-LwUv-z08e<=CwqQqJIjz2&(^X^U9L@SF2#Bs5f7GuoBuN?D<Xx%{HT)_4?WbnZx^>y0&XYbL(d}@xJujcaGYo>ChzS21q'
    '!@nv&)9Rrr$8$;BH_EoR?A-|MMyl07`{%BGZ*wUawo%qNjcasHws?~^xXL^5yxBG5Y+sptYOL=;{}J~=TD?V_7bo1DF_tt(&LhP044Bs}MK4)01MQ`>KIJgB'
    'guTVNC^Khj@a8fPviS}fxl+O9HYjcvVAmnH0oMJ`!{)40&K1GO-1`18*Qv`mw7#F{ubrZo_<WhQwr?_CsPI)*u2<I5bI<E?(;IG1FGFYX(%7Z#+jRcm41OTH'
    'KV1es#2(MZj$@?M-KsrKd(AsfsLx6l<5GxKg?EjppZ)y0Hs|cv_zGt~?y|o)VB1sFu~Y8|`nP{TU-^u<8}Jng2Sk3A|3o;|*nXfnC5}t1g%Oyd*YeXt_q})N'
    'B=`x0T=HY}>vlb#`922=<M6(>c(sJ={AMjveEqu}jAIT)L6doBQEsaorN#<xyisoyy{K#K_cyppOkPy<Yf|i5`n-uYJ?5k(hI!B*!B|F7w>N5b`e4I<^L>yn'
    '9eYCE1Xnc1SCy=?>+Lb#3A(D@TeT|gP~=69u{_DKQ8`6#!q+7J5{vi4#GFZ+hhy#n^Z4I!PMgChz#7I%g0F!0is(@nxygJp*E@*V4$h6-d-x2((k=}ADf;2)'
    'PlHS?`USNQtT|Qkf0xs-B*d!8J`g(2lHl+WeH|V-W<-6zBz7i^%ZGD*pfh58e8};V;OUX~PQ}{BF|1LkG;)}yW;RB?7Mf?Wb=zaY{D$0X)z<T7eTvd?16~u#'
    '`}6*r`MwdWx)&dZ_trgPP81Js8ypzl><{xC=O#uui8_Yzk~POcP6g~6h<IkfmudN|2ImFESaohyy%+P$Nqx?I0o2oL$oBO9jxQ);&5nv+DL!VHI};O_7P}Li'
    'LKxFW&O{lfmVRZtZ-lCmeH#d_oUhi;=3}#CKIZ*A`z-kxocSuZ72}l!8Tve|RGY(5wLGkhOpb4q3&%_!GeZv>-0;}z>U%a%RxUc3J7qp7ilbQh+thc~xk$uL'
    'bc?gG;K*nDNZ$8*ys^So68#eX0T$ys_10EQ3v2m~fr!IqamQ-RMq1r<25!c}(l#{jZrP55X4LD&bX<R_a-H>#c`uVw?x(eRwhXB)WSw4Izpg|cb0zEi6Y?gF'
    '<KtR-!FqADm2qAJinsT%7R(9q7(bz2FdtKpQC#0qydlO1&z{0$8T~f|W_4p?#M)V$l@bSM+SLMoDc^-~28GUfk*^-7SH;bZcu>@3paXF~pKh-s=JCm{C5~~q'
    'RgZrqFs9`L^8+~dbKWwpt-0BfeQ)mlp7QT}k1z3?^14}F_FQ4kB$=0i=OdRH#`|I3z(#t00^c3(5BrY33%Pa;^=W?-b^4xL@~Zv8VAS-m7d^<nUe9X|;q%@w'
    '7rl5`WAt2l=stu0em5p|KbP6Fdvc}p=b`=@o7Db1SU*R4Ddik%Y{V7pdBixWbC&K;aDAu2Un}t&#50Dye}051*gDvkm~!{Rt`T-O-v6^G{Z+CB$JD<JIp?v*'
    '?_Z{l6Bq>K-e4XorO*31e_yLQ0mkatt}^;75(eM2)g<=rfHGk*?t?rQc!A*_<bnG!E34SWA7d1mv-XqZC)mk1#D#)v(K$-Ad!H+4zy6JJQL^W@cAZ6gj5UR5'
    'FMw}^H4AJvi+T#02k~!(RnG1uNIdk+1w!joJuQ}hn7d(&1zj)WSQ;ml7Kh5v{R#sj``M;oL(qP;b3YnaGQ}7_;|h=XJ}uA6qu*d23C~72e4C$?Yan3W10VMY'
    '%=wo2gki3+p7P4M9QCYQYHy*uM>+Zy{oXa~iwoX+J98{-P&&7E4g9o$<f`+|S@G3$+=i^{_o7+Iu{|798mAFqatRyzg!whKUN0f_zLa=3jVFw9*e?%!yTg9!'
    '3m@I+pDE_<AoI0w1|QUUPUCX9mw)&JobKCoFZRV{W%2@1-_&jJu^eP_bYU;?BjTh4FDT;htQdyCKpAtE;NCN2oWSzI`6Uh=<6SHc9Y6PnxrfVY0Bb62J<h|3'
    'dc$l^be26Rx9J@Uhy}C!c9tJCmIn&wl~EsguijDPd9hDmB0hf;`~$IEhw0xaAJVKxm-Rjh6Rf!7<Gzixo;E(hkNY~l!Sng8J&?p)WX+SPaW`Tdy3XCKSMO~;'
    'UwgSzwO?+KENw*IOE32cetZ{sHO1$m)}3m2#F-Ml{?h6T9bqgH^q!~c+U$Nh>1R&%HlM1vX7!zNyCVNM@jb9H`|6%4c7?##p>K-4c3Ag`y^?Q>ei@?28=U0@'
    ';yyrLOc1lOLF^vb1>6&_-aO3HxaQ?EJXhA>Q~f;X>%#&$s2SV2m;EwT&z)30616vl{sX<DZJb|?ZN&O_C*R94=7sGMd4KNn<6al_EP2lMMuy)a>M1ebx^^ZJ'
    '@^F6=kF!1ZJ%KM4>##$Au9Ib}f8Ddy!j42-%iQ~ivZLI*EXK{F=#g*|V-)cww#BFq5cU~Ld%m=n2A5T04P&xW)qj`Q3V#pwo8|k#--E@NW869hD(Yq{|IRq*'
    '_hpCTg&M=NeVz-p;VfNJ9#-7vLC*VjTy)`g*8%6d6&Dru*MjwTz_;9KJ;^zzxJ`;{3*%i+@=*?ZYX}>4i@gSAZz$#`rsteNUPJ-;QvDCaRHa=5&jX+3ax`rV'
    'JS)gmpg4rfSg#HCDfQOqM-9ng7v~_!%Xp2Pr?2F`TnERs^^t+gk<Ym0z_+V5hf@A(`F*y=A?5C-*%{`#ZIv^y)+>|CMA-+J?*LyXlDyBazG;(7S#HM7jk+c?'
    'erR(&P_J`iJta4GsA8LDeYKs3VLq$^_*2va#=s$q`>B0~@pSHG_)E%8Tyq&4q~{IPevU*;dN6dTPA0jZ8PBBgz|7$jiuf9>ZLG)4ErZ_<dHW8a_h>FeJ(*SC'
    'n!$f_SSIVAQ}u(;ugQIAa;Qq&Zj6H`IS$M<98dI2zhiLm(B}zm8MJNXTBP%OTl*!8+gm*Mv=q<1Gf%1GJAi-KD&u+O{v8+UVNvBgz0YTqWjmh3=RM~=%6Ix?'
    '^81pn_a^lV$iUcsQZeV3u-O}J+#cG-ebmFoUgGuBxJ>c6hzVKyjO8ccWiR%=*=ZS~=2*#)r|fuox(-Oj&Fh+9Q`&F7b+(Gp8}dA`URCqa*gDu#`7kMT9vk7w'
    'h{5zD;&RpJEgV&wuOrL@ju1ye9K{$H1i7x~mHIuQJRsiHOx~+d)lb%8uWaSsmvtfi8$Ua?25|q0;HRhW!Ty75BZ^nNAN1MUN!}^+>6`I>lKOe(@6*=8^xqR_'
    'SYfNsp1NYZi20RVnnR5Fy%ll?h2;_FqK&bGWL%5(dAzR&_SK5#12KLA`|01A4-|d<?qU>A7$ZdeIvE$yW)(h?kmnL>xpfPK!PGn<XX3;%ctF}a&`DRj4)MGB'
    'te9Ss`7FDqL@b76^ixdW^>ANHOr_|V#{AuMX59_O#a;(kgFiXFXe_cdUul?_w?Zz_50vMN@x`rJkFzw62GQf})9h<z)nrhtK;XgjoJq|1;Ro38uD!NutReW3'
    'AG0lT`S(AF%cjuZW6sB}vvslV>6Xl6=sdI7v!Z6eewP`{-=a!<2gpH_qka{k#I+jSGQ^YO9AFKpjs;`x!f%ChOtOt)?pQ}k_apw1r~jnf*zI1eOq{E!`07>N'
    'iZ^0@uhj9eZoK>KWehV<GMsAYtl05)dJkss;wZUoaPf30b}+%-O31a3JbGK^H<LOvCI6=G!?BZ3>fIy8hk2~!EI)Da9Jh&Yrb)K;Yz${*sCYbDcYHg$XNlvU'
    'u!jfe>YHDfe2SQj`s|($gzpJ+kq?L)uCHtj;x){8-<E#ABfSIlm<ec4waSfXJjg|et4#6bEjw1{l+yHgPUmKm*rgZ`plMJ223y<1Jkk67DsAIWH5Vykx_d8s'
    'etHQT&{&fUot4UeYS^rDF1t(P-MRiIX6v=DP4yniPP}{X^z~CQkh17&kg!R=eD93diP{%ZkAu9gVyz}})4tDBYsP*Ux6roL$?oBW96U|QU5U1*>6e=^&~OAk'
    'U7*fKC(#RxxB~~2Us9Zucn??$@Rf@nSZ@!{2b)K&9re4Mj2CCbt>F5g?Q^dt*?3<yW;}DRniKPjTIBTKTmG$uNlA-Uf7&q^6LS{p!8J1bR7nnn!B_mS|97qF'
    'fzOJFu`dUiTrz(+=f;^beMvYwW_%&m!}m5V7<sWP+q~l6BJMyjp7pGD*$X>JaKp16*Ljo1!ME^1T?cHM55_(x>=?!40=oDH&t!ahqE43l0?FIQ*fr`8_l3_K'
    '{(=9!rF>MPhScC+2-7(v?$x|lIzzs*(U&B<XA8%=d9LTqNzT)%Ibcp|v33W_KPXO`Y)*zr0do{!Q<jfz<GGTI*MBem*$({Zdoj!($Hg_zjpl3|h5syP^pi8f'
    '_qEQ49pAcd2k3a<;V1VXAKfwZFvhCuSYzkjsNUH6eyyHipOrr1zKW{P82Ywwc0gaQ-~0~s+mrZ8HFhI*@M^>kq%V_t9~&cV+a$JMb^NNgo#D$oruZ=!<3$@Z'
    'r|kg7WTedf4|-F`dm6Fv^f?hd=Tv_aA<HPADgKRlr!*e`W6)TSHOD;m2Dxe8281j6ki8mP<;YFvY*l+m;e{pts;U_m+|0PvCg*WaaVPhCg4cJCeESpFfAc!&'
    'Dc5)04d_a&9sd^pvf==UaXVwIV&Y>w&KV4I?nu51<L;}-Ic{+p6X$Y)bTvP3*P*d3o^zr4O<C}Uiym<{7mUc0UsX>>@Tak#3h`6R=OAu_d@SXUirjMsk2S1E'
    'fLvFUL;SzUd$Z+4wk=!mSFWAcTULfcpa?Isd=N<VNTPEPFV_SJaN#t2BZ;Q0{=L?0R_n_ViXA62Z&g|z+aU?OS<PIt8FLJoUlMV@w8c5V@R;~sL%$2LN)W38'
    'RvpGp&P&r+H?1M(v(wk`@KD#^Tf=0<Ir8Id2tIYk)(WYiJ|*ULjIrkNm2<g>31dEZPxbQ48V&tD;~5w?b<LPmyWAIlVNL)`q<rxb<hC;I3w^>5*UiPnH9&fv'
    'a2?{{&c%ZbVI#$w!}=NW8k+jhr;_lQD~P$SS0xS(<9@)e0{2G^>ZOWndZfpaxTJ~yk@pfk3u;l!&I%Kh@)Jx>Z}~nQ)->mJ?D1EPenP5KmzQ)6w4J|P?rX;T'
    'Bj2@J@Alad?ZNNl&!~0H`jhFkeEFGDnoyFbn{0{2C9!Az^AE^dS+!W;<;fbG4ebHcwawIhPd(QGF#Thv$4Z>1m&Q)=hU0$t#+XSx)|)K-L7Q88HYZN?<Jr(j'
    '#9EvDeO8|g`i|ngMoPmr`+eN!_x4$)uPgTVHDg43H^rrXwJ&*Cz#c`PWAIhnX7F*Oa(Ft<`NBU`_7t#>P3H9&*MyG+IB{Axd$t)L;+#6AbN9Cgvv=2y*NDTw'
    '?43cKiCj18_8Ot|vDyo$eTi#SH1|8Nq4_9}xJ=+0mY7hP$L+t4Kw~?oyh1)vX+o@;Hb47IF3kU${}liKb2j}s3vP0g*B|-G&E(f~o(sa+>#zK4u5eSB=5Ipr'
    'sjxWD<%_dl)51;hCja^?M1J=7m;LI0z5G30#-G1O&^}~xENsMu#65~%BM6tp7YzWU6t<n^i{LPBUq;|maPh0!NRZ@#jEp4TQX(|}keGcdo@G0!Cb985bZm{E'
    'Kc@#x$c_u|*7$inFo@L^;a`)1P*^m8>l>R~l9qPhIR^+{Sz5n~An_{>q$v??%WJubRV7YtkhAZ`c@nKd5%97Ut%~Mc7}*=Z-Mv#>Zhk|=6^u9tE99{eE>De2'
    'V32iD3k$?Rl8{Ut!d50Z1AtF%n1_T_*Ca=J6Zc(B2^=uvO@M*@Gc`pKm^QDH>U%Cm<GmpHLZbG<u>@xp2paRz9*Q2&3_X^Fxzvm*-v1WCVju_)4&ta<oPy(G'
    'gWaHkL#)Aanv8+SSfKfVNUZr8#kJx6x_I#Vh3ZOTyjJ_s>1YrQ;dPLhSd1+?L&8Z~WDvyQI1Tgf$!mm3S-Os!Oer_8*a*ZK;~oP5`MX|{EOEe2*Iff}Y!GbA'
    'd!Ec|Lhx!G@R`qQiyqVO(hZqB*0r_@hQz^gfJ8Gj2S&?rnMgDrp|N~O<mXJ9cjE#cY=Ace%m4}pq21%pxd_lW8N4Mjn1}6ij|=@a2>q+SOVD8466>R>1#;o@'
    'dju`(W&!}e7pHk@+@f{G#7TV{=zt9aCfNWTKrYb(f&AkYJCSzbNRb>lgn8mH2ge%M!z+q<j(6kHj6{8~F-|-eiMEM@y5RvXpv`s&B!m;ad7$J);Q<J1=^mrD'
    '6e2aop^HsXL<ofIef^B&EPi4{nG}<A-r@#0$EJuiRN9ZDcd{*LKbJG{S^60X*#c#2#Jt3PI+K!DuxG+<YJqxeo(hD6wbG>bn0$x8FB#wMjplpd)I;(e8%6oE'
    'm>5}MQU@BaP+$wsD%lu;ja7RIxR&-#JgaIVb!;Ko<=*Yz;@>>(oVaC&*VABOkR?VFlZca=F1_G<)9J#s^9)TQwd9OVQgYw210>1TnW&DQE8+xK0ULuP5tecw'
    '(b>Vq%&ukMt@n`cZWVJ&F1Q05JDs(?e1-r|>`!WJAwp~CGc45Dl?~a;=R!w<#Z0>(KZoGTVPGd1Nx;Pg7P8&!Gnmb^1tdDWOE=LvV(d1cU`>SF0Bl7E)7^7K'
    'KwATcQ>-0dvRV@<dr9=`Yg*^63q`O?O(GZ+U9m=}ky`{7NMujXpGn?9|9h+7SCAB*fSm{kHtyBN{WSpx8AyHCzpz0S0HU-PwOwh&rP5@RCBE90LaikFGb}2x'
    'WYy3~J|{Tm=&zb25HDC<YmjK;@aSo@yrR)YidmGka66BUwRbMeEdx}14Z$Q(Z!{0<&*n+wo7X;x67agF!p+h{;Al0Pu>I0!dwr#CyEz9aSXK>oKu{vanaPtM'
    'cH=4}o|g%qse4I`VcKE<Jp&Ork4d_aLCFJgHp~(vmm{(=B2(t~8RmfqB${QdaS067g^ESo1%DA6WHF&4_U9OE1;i&}P0wJ?m*Y(rnk!QR+gCm_0kJd*9yRuy'
    'GaG2}d@+hCBo6KRQSx);k_0L0<T5mB1Bj=Z97cILe^;NoOR&KJ$e85!nZPs#X+jf*8gB)>HA#+CgT4e@Fqxw1(<9&_-I)KpX@HI2QUS#D^BN}rH@%eE_lhmw'
    '7Vodmr|8_Y3K14@hE?>u0*JNHg;e=0Kz0a;Vjvuagh@^kp(zpzo1nq7pAw6pbAcenO&7%g@N5u!M7&$^8GO1bk}HBAxgM`Ap*>9m!sC`c2fE2D&dGByo+9vW'
    'A$>$_vS~3m5aX<21xY*V8@o~4w~yG*vPm^+BK9UhGHei)&xOtNd0}I#)I2YVLlk(tmKGWquPabMfprApU|X9w34p)JyYQ#Q{%k{mW6eeS_4LHzv#?k4s-dXf'
    '9aQcw#EY__8H8X#><NLw<4yaMnu(#wECjIZ2FSKOKshzG;Ir$(il?E<Z<FZrWn+%`ER?>cPMS+_MX5sMK3Ej8L6aahb=W#hxGD}K(m4W$hEF}ckA~;c%pl<Z'
    'at#b1v5AcVB_n<;?WP7fqdCt+0>%Pem3Kestnr){NSeh8p85fvr%K=o3U;+*F3xy+Ekbc+*f<jyDg0Xs_9&8#JQ1jwpalJVrhfy1M@#uV5Km?zw3SJCgTx#e'
    '#GVK|e2v0jvN4HqI_YL<ZVCm_3$bT*;;bV8sruc+q+L;9b-7l={uZ{2*DJ=;wYFHJ?`kc<eAh9L@eWP#Qe=YOHP|0~#4f)1j&uwF=h6m&v+U!x^3q0!m^;GU'
    ')p~3dtjwglb${vmu9lU~KoKS<ufkqF7qCek<8?Yq_P*SUJQH<uRk<-T*5nqO<a$adL5i?IGt`QNCpD?2J1?A@f;rv;_-eccy9vcEBqGyf1pNH?w;qxL132&?'
    'MR@G%9M7#G$Cko&K_L@-CTbjoYr{T=hRwk{60xm8Py&VMRU_n;;}A_GadQoYTw%WjP;?PK+}|XCB83iFfSkm5_*`&p-7O1R|B9bCk$C>jrv!LUt4qj(KyFx('
    '6`26*E1$RX0<pmGVL3qpGhzRvAOn-P%4gGOp%u4)=naZ0pl|>`t2pyvZd0NQEcSgG=f(br7xHsnVjZvT9tQDv*j>RmcE$W!4U|Z_qJ@IQ`G}+(wBD#d32Q)#'
    '>B4z0o<Ep+)*!DNv9F6_-*v^B>vEIeDwDL-Xh@*~ImV{cgp2m9+}KY~0XLp=epRfsaek0$zQueh<})Mzee_v~_e%L9Z}<83-R+@WzEy&5@|}R3WNhYaYhn-G'
    '-S39y%1p$tkpIek`JP|QQ*T%l&yhg<aYLj=5fY(6Q!U0ZP)HWG2JMX+{}SXEWjtp2$zPp~rmcjt9Aerj5*NGIR`#CGjPeqJVfrjuEuZ@L<QMvEruL2DypTQ+'
    '+RN$bSKO3#`F48|RFipqWs~p2th_Dxm)z))2VaH7LV>Ux;xyq`IAr|3egE)+q)Ww(S!+;aa<v8%{If0B@f!LMVxLjX!tgg}uX8b!n>I-VuCkxF_R}r`n)$Qk'
    '(@4fJX1>jvhgHQxJd;-sZQ*w^i6r_N922SgT*|wLPb1tP8Tp?JdFS56{psYJl(@s^Bng?!89+Q%-kX}pm{7WIfR)GP`nUID2kI(j&Ij@|)x!d6Vc_?Pa}efJ'
    '=HSKo1hEsu7M+`V8Hq)7Uh*PnlA3J4lON~|i*ydhvaSg^@HXc&%(~Q{_5XA(qI<@I{097t%G<0G%DqAYUqc`#Yanrt`b<rUnCw-~g>yuHI~DzyQLe#vpPiR@'
    'hX@hPhHc3Ao>eZYytm%Ly05UWa3%_Wd@l)nl<oKA#8RYUc5Md4`JjPm`Mf@==cvcW$*7mi(zQG-tl&JyrMxOP7=M*}!)s^n(`;LJZ@z;R1C@S>LByAPfn1uA'
    'd-sqx3V&j1)=;nXFHzVE*5O5e6aMxZ8v+;AdOW(j^$KYivF37i{vC2hdVfid*a88-llDUlAqVfh>q$7D*1-Q1=P7_N=p52Q&>%WZc9?%hqKvOBR)ONqLmVF|'
    'dYd(;2tX2NRWSAw7%nG3JPzyTE*FY*xF1&~5wIaJtVH>MRR7mjeohK+>+gZzrtlT3=I;<^@|huGStXLJMHRgQj3D~F8jn<cL;mdBOTSfT@cK=TShjd>xlU!?'
    '{(T1IPnb(W3<;6(---Lj?`K1t*?k`(xah5v|0VY&G>ziE%)mgXd}7V#2dq*}`SQJt8nG6*gLnh%HT?HZHU?WcR`d~cBRtzT$gvhU2H^TmJr3DMI`&eGBR3X6'
    'e$N%QgWmz5c}Jpn&@BJ3o6vtnT=6c>Hfcv`A5KrVe}m0-<30qJ6~^FGuOvw=h&}6iC%)^VdCt+hl@}^@9Iua`GmH;sVw~&Sdc4K!K}^@|QN6b*<CIh5EPVC9'
    'p*h9-c?DQQzsjg3mgM*2*-)&VvWUy_0O|`8@Y|-ANK1r1g%ZTcid*G;Km$hQopb#-{`_y)tM0Y*GdHnbh=OOz4$!(@>_%?RKkNGP8I0>_-vd(H60?&Q|HSjQ'
    '&p<k=vEzEvz;qO-11u2`uzb#_u_%RYYLX!PE(7DVwQ3|aw!LBv)9-bx*E*R(zr2^r1Pbir!bc<q0iqDXF_fZcK|s7DYUAHl4^RD7^4LJ~rN&)9SA2@1&I$y}'
    '=jGB*D=!LuHVBgoUj>9Z2@Fhvb|{jI=fLe2uZiT6jrO2|b!4)~{2j1=BAz|9&z<1mZTjPJ+AJRaZk6{ZiL0PI3FQy9&__KvPWepYy^8ln@jA*$R${ctBg{`z'
    '?Lm&~32ijQpdvmH=U|UXP4zr|j8XJ5ZD$c{g$-A-R@lhNvj{M6R1c^lu}eMv@d0BeiDW?_2>{yMNVNq)b{PM9+1GUIyC=pa!iTKDdizzsYtuOhxh6hK8t23R'
    'e2g2vo=)2huXX%9t#y3HU3QF7;=R$x86sTbT@uWdPE`&LaaTa{a~PA5g={F}HK)mQxo(Ho6!Eva|1-pfg)Pcg)?>p*=DjIn7Qa9`W3ig-pENa*3fb*VGIzvH'
    '6rq{fuxp5?yJG5Q9#xXFGGSEd7mFmq9!D~Ewh34pl<%zti9I4*<uqZC^}Mlcj9y(w(qJ{ABuMgT<{i}UaSf$K8La)MpIgP}9&%Nx{oSb?i~Ai#Xv=wN`LlPQ'
    'd4wu3X11UETjD74e%A`GJH*yn;Gm@&xP0|QBnN(B(HhS<=sY2ipmd!25|E0Kkm3ifO(0li(rG&TSFRn}K|YLPjRz@)w`T%f6WieXoDxOCGcjK6X+5vfF`s%4'
    'S12JvEOuDTqo8ovCZe>7_SKl-nltNJM*S@NS(G49^kXXPd$7(;3@pTNM8u3S7~=Ti+&i70f!MRq+;fy`1AMnPuyZr4{kU~b8@p=k5oqo^y^Ko2&V`)w0x{+2'
    '?REGdd=e24_3B!%0nakP%^^k>#H9KCmCzV85Gsrb*iI%u`9!kevi*4_d}NHnZ?17;ZVWMOZq#@Iyq6Yw*8ADa6Z+=K#;PfTofN6oH6rU5a6gJLG7nfPjDHo5'
    'y`^wwOR9s*`geW_{_|eil_oSn#;G+ZeCZ;Hu__!*syX{BtdVVF&HUs}+F-({Gj4@!q(#uCh>aP$=f1BJNcUjtzCncJh=P=suR}PaI-lnGrR|<1X0d8x@@$o7'
    'c4|D`SkJh&!sBQW1VuK?xd4L*$~e<td>$Q<a9C#~p6MZmVnJZJX9&kk`DiUbkAB6j-Ve<4AH>tIvV#$k2WVfWN^>QAQK)Gd_3}kFj)MuJUWHwv7^nCmYz>O2'
    'sOLkHVl_@TUkOZ519nl0V5#RL(O$@B>(6U~JAc0DYX6|0J7$xKWQ-r2g+OFqXyh1c#smwdXoZj51jL_FI01=u^k-$hUgq>5-?_@p!TsU$_@bz0q3*q>y{8FB'
    '+awXRlN3sM9hKZOUBsBXgvDCV3P-o;ToMvh7!qjU!tX;vd>NX%uJ*U`59DtVX}RAXNg;I)=2y+z?z?Nn@HveAhSw{Kvju8s@6TTG6+Vw4H{{}#)8v)PzZ-dS'
    'mWE)WmFFiPHsXAYf4wzrUtsw(#GlU^4_tf$ayZOqmOc4>evf7r5xbsatc_ZtM!oyc^*dVY2M{X7d?m!ubiR`>4(Gf5{V(r{@8RC__0%x;rX)uFi$sne!tV?7'
    'C>v-!AMF7k@0R4SluH<+Fg@n<jC%D2B1*kJvvS(!=OGdC%JcskF(fHM1)>P{x&g5j{C(ONx<pn4eQo7pf0Rr5{rh5z1QUKyj#k9WVxN#WOMLr3pW@r&1N#&x'
    'qHro=j3Um%-RpXw{R+5uKDmJ!Yu4Vpc5b9d&8#NQv|dQIMq{16@EdOZ!iZoK@%f-<4Ds~BSFe9azX_YH?`|&&GVAk&#h7W_z9Ys=J$d<MFj-Si9tw@d%l$K8'
    'T!i0HcSVx#?hoy^&Y~&ip?z1+fo*?|{UYlbP2GkV`ok9bZ!QWG^E$;bhx{wqowpaj?w>N(Cgee3JqWv`2#4Tju#vDwWIf#Wlf@p_Yexxld_k0zA=;zQekI^}'
    '!JmRP^!LR*qlree4Z2M=ZQlrAi~1K<jpewq+fDDWH(+1CkDj`#OJwNcyhK9uBN5CAAM;C5iOjtNu^Hg#JCcyf5ELOiz17q${9V}}i{rOY`7kZ+5y>2w#(E3?'
    '+3shhn(+<$FR~sk#ePVv2XI&79jRxvg91QPvv-x_6co-<_6M=?Kj*!JA?$1RyB#cC{8;rHATf)okE@yz<MCzxo0(UU*STt(-}Z}oT>NVye7L5<oU7W@rFx=W'
    'lM?4;^Hegi1vjahx`Cf-R^GWTD2cd0Kn&~#=Bsi2;Tep*1Q1`LKdfq<!f)S9s$twL*Z%^aFRQN1H9ffI`E?jTF5_<pXK`&^lg2r_{{^rSrwR0;n*>*U20m^S'
    ';jC4=<+1w!!cDHBfRIuwMYQT~JLnHUyGdePGG0D3uOmI;|7l<JCKc;t)RiPr0mhSlahbg{#&X^=eFeGyYz)({xLNqd_)woSjsve#IJ-`Y95F>^{41Umgk6w*'
    'b*K8L6oN8wKt_Lrb-m1*UaEc<9>jW<bwqJ*Rlkd`Sf6Umr`Dz`7MbZIa;|CwWRKRf0IplDb30FCeQqb#uDBP~mY^nxV+o(Xb^EjBz1n-j#-(!^SprL6^cVT+'
    '3FNCmjgRZ;hA7jh<E=U9T8O^C6zp^awO0FVK`mSp>6lvg`yg)5>NUYNg?@tPdm0A--}9|SiQm^b2__V;Vl4#nF_*EG*X2V?I&Oq@A@+FzFl)bH4N5#4iQ|Yk'
    '5o;4TpZiY>10Sb|%}xJ8p2G$P46h-vXE(OU-GR>gv)u!46P*8kuNaYRB6d4@g?xrMzcMhUaD9V|q%@c3s*UT|r*Z$j^Y`LB)PD-!?c-$~ig<ge@O0)g`vvL-'
    'j$I*PGSvs?2;G^_31{zhLTE=?PyYNb_qSZc>Z_b5?ytkvW3Mb50^@$sXXA0-^Vwkag$o~IF2zd0-*Ww7Ejl32Lyz!FRrnZ{eazQELZ5M*ip`c!y$1IMQoeAW'
    '8Y8$)$bFo-@D=+`2cGj+=QUbpkAd<di%bBy0C5q@*Xr>KpN5bh(YRdchd@jYIqNYHyh{;+{smHWiv5~){~^Sd*mH0ac5ye=cu$2LrL}2_hVggL*Vv%TYhU)8'
    '$$Gc4l^1vg*k;FLB$Xo(BZvQHYposdJF(}Rg?T;xgCQgtlMQt6T(Twv#D>I|LE_uPckKI;;=0pwK;ocP+B48&=8zql;?f`j40u7tu>az-WB)EOYjQk56ir+Y'
    'aav+L{uVyHh~sW`jvdwyaNq#v3E2k{L*AfJm>8F64Hy@|KtSG|agRH(zGBA?v4C|Z&WYj-j2WW}+1Lji(~5PS>bcW-d~xm^(bpv|4z$ldVO>rOP>bkTV#?4<'
    'E9MkQ1U0VO&iiKxqqchoi-a_Nb{~xMxPIe1qgz?M5$k30%=H=Jzi0NNwG6(P$F=0QI>gU_%O7cx)!C@~HUf?TG1i&)+*AV01siz1$G9PdHK7-M--5ap{#Og2'
    'JaP^J34&2Hx#Z&hxb}v4%Jn#|XA!O{d-<3Ee@O#{XQq$`u0Sk>Tnryz$HkaTv{2$7JOgZD%<TIUdA<Jo?eEl~OoB+UR=B1*MUqe4hoSksUR0WQ@1tt}zB1?z'
    'I$zhXYJ47Vk{q37w_C1Zt!6-ce<DAxWsuYfn<Dl}bVBmU{KM`=3pM;*PmaWU1vpu(?`!Oghto$s*9Q|Aqpe}vql5)`FT}4XrkT#8X<r-&pt=6!2-(rOT)%Oz'
    'Epu&;Ys}R##(+fOAU;+;`rFP|^L9S(<mkMyEMekR%-=g2xJiXWQ0uU@W}%7lD!Xojd!~Xsbd=Mv&;6_z8S|7f?kZzlEXRH4bIkW<k$mf1E|D`!%y!Ml&1Zb~'
    'J9A~=74$sOfA?md!F+!o;eG0P;XDy%gjrve>%m$3Moe`?!N19-6Sm`(O}_1^r<><sns8nTg0dKn#D3q0^4$3buAndbkc^(Qf_UG(f%OCa6JKwJ;fwmkem&3O'
    '^HCi?(yjIDe1YpH3m?MFdn;f0yERH8-!%lcWUnyfb;X>3h^Sl##8YPZ^GK{M#vKLr@NBMD-o(5}AL$a=y6Zc6*Ol=*CytZ324merz8UN?#^S5JKc{B=-M|zm'
    '0SMba{Pg1v4l%FY9p!&JVt*N;jw4%yoJAVmg`a@Ft=Cr(zS3maSUpRA!2a%MGv3KMk}2eHL+@_<E((=Ew|Eby56~}`4jfs{({iFX$IM(!lr#9q20npznfC>('
    '!rl@yT~&IW+_Dj^qWk&=#NgC*-}RVv;*7cn#>W!3NIV#wMQ2?(i+xM{7_e4sGU2!SeKCP+6VX0ks7uW0QG{M|)o;lBTsgw<HUj7+0RKsjBltCAu}>yIBHjCu'
    'xz2@uOnGX>ku-zeVVexxv^^U8E7p8zKPfoa-#9bp=HPlM*D@f?(KW)MpD8i!IJ9xDhW%o30E}x4?5}f;y4;&aJej&iW>2@a`POqOp=leJc33$4FbZ#*v3~65'
    'Hn5W(ab)aik#!WZ+e*w@iRY5g2lmq|-qO~n?&XP)&jxV=6Z@ZA*b4(ZTj2^xnHkHDz44>AmG`>lc`x%%WuA2g&o8V&%KuWVRa#9@>=v(!*jVI;sCBo9sjNoJ'
    '$tF5*51L#9Cijb(&;1GSfVh^(5TJY89$N<9`cd*$h#1a{N#+)AQ{y`LGihEG-G`A9hSB)fU$N|CZ^b`I<hNq=vJj7Jt?d(f{<8LI?_;C4na-r0!{1Y!RKq3q'
    '!T8tN`K1~b4E!$;y2nk*OS#t~9y)8kga%gL{2Y?rgAeu_`3~zkQdERvilwT1+dh)tC-T<a2ur`eRR2=GsUxy$_{4L1zN6uHrgGljw+)N`_z#=3JAd=@^(OyX'
    'DE@f)H7^$C)0g?{Yd-uH<nzVX*PoO8tRVg{4}ZM=_)+*-czM0az0QL9^k?`wp9b@pCheYXH}9{<_unGzz7vb%fJA*Y1V=iA-MCRj@Z6CF_wxh>9)QI)ATr&7'
    'Qwe|@Xh;`_SL5@ws>r>eIT8*{P*nH;;??q+z}BN6T+c939L-0jTK%vH3Y4-y)ihNP#UL6#6M41%IXug2<K$J3yXRP67Y{j;z}r+0JM1#l7tyeMPK@g?!8QT$'
    'NFh`>3)npIHtzVZV>10ql9zD>RdKUOzLlz;@m+Rw4-2tidCU*{dWFdt<h?nOAk|Cs_;7;<I839uP5Pa8h)9k3(CWs~Ty7+bU|a-BNR$Ms^kpmrL1JpI)xZy6'
    ')WIS8z(9*tr41EaOrB9vdFeJKDS{0KKJP3N-}Nuc!J)PK0O}!c6a)|n{mZHJ27n~_yO%XM2k6Yf{e)4jGuauDxYcWw01voPo7Su>3X<|XG#sC}2sPJg79~Z@'
    't_oF5qC>_Q6$<jL5h;gI6CDthgP{qe+Gz-6Wa3Z^K&=|S)9M8P=zk|?ApB?zfsica1V{oYKVc#`yjr<Mi~*|sLNl}BcsXi;CRwA=90b3Y29vM*Ny;Hc(U1e1'
    '7N~1lYKvykfE*7V)7&d7w(Fl2s5b2YFu_RMBrk<zI0O!;ZoBkc$1L75xcIuFY0rE&a`aL4*dk<nL1G>vl56GFf&SC9pJIRKsWILl84}+!n@n^<8r4qKxDQV>'
    '#uiy~)vyCZhzDatjj@&RFW93{7;07<NQebZce3Fdh-Qs7yl9HQ(YzJ+)F`F?wr8>``nR)A)N^a{6-?%GP#y!ImlWYy-4EB^QWNKt36R9if0@@e3((w1bpq8b'
    '0va_wXD4dPHJ-y6la(w67Zk}~VR{+R4}{fs^-SOmDILSTeQS(K6Xl$x@2SUtN!)b+&!j;J9$lK>LBe+Vd`%)z1hwp0itN)Iuj`!p$H~bh>Lh|rb8clIra=Jo'
    '8Ekkgl!{=N#5?1^fu!)YynKUk5_@GiBVt+*KnN$*%KP)WyTtK3Z*g9RC-eOpT0tGik|2>+V-i;?j-SODDfY*B2@0Cc6oEleE468o?3Denn;G<dBzgRmHUdF~'
    '^%@i%;Cy&cBvdMqLL|sy&qI+iFv-JYL$NpqNQF~^-^_h0Y(_jOEO6iIbpnLtL`c`vd&mJTMF<i%?e<hDyIJb7JAXjL-$#H7{X3f&*CxajbBRHMZ@t&;^`2$N'
    '6KvR1#cnx2WWyOS;%Nr5ZHS{lc^1A&f(k-wWvAb1O$Fq?5=j6hRi&Dc8$5hTUV?j(p1%UX3|@-}_EAB%QXKaYKO5^;C+2w$#9mR~HGoUU*)R&2gvn!r!LNa&'
    '!|&N(w4dVpGbCIAdnW8H|9rj5Jw3zN3#!7VKxH$(Xy%vidhPmhKTlwv#9Y4?ft2-k#|}7tjjbR&Ej$E}SQTrC?%4|re<aY2ryIL4rN&k^^`*$Y(H<D&U2-f_'
    'd)W|C(f+#zLs0UI*8D*g4AeVX#gRP{AhLQbs8v}-W?P9(gMVIGA&bYM-isav{h-n4<SInSv5}3opvq~DyCdXqSiBZ;JH5V`gj5PVm_R9g&FsSYdK^ru$GJ=%'
    'OVW01j^u>Fcb;dwiT0C9m^yomX4cFCx|xJ`?`b5*_q2{|l6kaWi9By$KjOIziE1EL)y1~{9t9Fm0H+t|gwi?+rdX^xE?<T@1!hu-Ap3dJ?<W;m<J6gYFo|NE'
    'q{&Z35{L?JL1Pd+H2SM)O2MtZDt^|hFjt3#J6C+pAOXkJ^;tH#%WIgFqfsp4%C#HySCwIBkhD94M!QRdF92FOv%)RLp-8R|=N>h1sBs2xC7m(2_tZG2$;`P4'
    'y((-<B?Mw@OmrFXznOE8w(etEi*i#7WT%Lh5()AXz5|MxG(7-DsX$KPK>3^^)>n38uw{`K2}GBoFH%ItA*VDIO}paWkhE`rqPWvwi|3<Mz)gu7A%Wz^^{49J'
    'NUGA>8H*Uz2Xc8K@?xpHnpHru*K6|d6h)1@ZWKOrqV8Sq9#!8Le1@@VF)JdQV4v7hqGlU)0Z`-x_uR%^L#%|w^}rubo(mD}i)#2WsjYtUKI{zQ5R>zr+{yv<'
    'C;}o?6l>mxebv}wkLA98|DL>RB}cJd8n8a0kbG_>sPrq<RligV*MM?XbF2>V-3CFo)W09ZN-Y{}3;15_tHKyRkFZe0#VZC6#!09N)TBalV>4q=h|X1kc%3dx'
    'tFum#IzddikBLMW?gyU>KltCUZxrZH`xOQ_-2)Cx0eN*{bH``tUH?8R^`W?#VT;86g8UDT3y^VAUgb7MrD%LcY@(Lq#sz?Y6_l|NSt$*x=dS#78#z4fqzD48'
    'bxCN;IjbEhJj*e$;!{UlBixTsTrI&hA$(~N89;ERSUd8)#JoiXC=|{6S3M6m0<6COJW^p)3ahOuchP+^6m~yVFtS0~-y_Inb=&0(8lKBuyC!YWq`8r66jaEM'
    '3HtM#KYT%APMs&^_m$@tj0H53VIiLX#v*XAuBiYrjcu^FCY>_&Lmb@cHRJ+v(VvrRo7UWjMLv?8MV=@3-%y>g*bs(uZ`y$VAn6Zs$z%5f3aVOsAzRqb_RA*2'
    '2!N|_mfU2#+h3CC4fhrolC#D&O}b})pU%-Bx$u2iw8x?-l3Njpk8CzHb8c3F-X`_9ItCzxW)v{w1`N~hr~H8p%A|t71VtGjN;hUuA|o2;g9KdPh&42TTB>xv'
    'Nm41DcfL&UE8@fi!C>o$9RjsILTD*Ji|od{g@tSN{bGxLN3q)|d+ahrodA44{J|h@<GM0V5MwpUKkVc>_3iiPA?4T2F;V&J>()B@H3S@C@E1LoK~h$H6KA{1'
    'qmvyHXE>hUZAn1B0dT@3R$@jP;LP9gKjRF9l|V^4nI+t`lqQjI=gk5gnjma0#<RbT;xkY;W57800W@Z~{|sel@3R?aFMQtFE8wH?+&_el5D5$E+;029gHo`z'
    'fp3RJFCP_^zf?1QqV~tYtTpDoQoO$P?hCuvt$mG0*+@b*2nG0%EC@*SYAg6V5(RyskS+5^ODbp+`S!>n$hR6*h>)x7%kN*2ldN@5ub#$~5!i=ySm3=lM^G%2'
    '0b)~<Bt7HiRfzc}p1mjqK#<r~=gtk0km8&_12*I5Jc}TQK4X#gqpw1~n>trzt((q#mDl>5R6!tvNu$+Si)S+WZet*>hMd?Y=cy$azM{?-{xrn`U`{)^%3~)N'
    'wfpx7kjrYsmGhdt*WlhRh!EEd`e!#lq5yLZIePRTk{rB;nIlu5QFe(0=P0-5fUti@HQuV(pNf4m4w1wI9$SAYSEqA^D%Jo1cJvPK4nh;NA;l-pNY+??pX3{7'
    '@lJV;%fvYk0Q=xTK*2_60GX)fjdj-mh})sQZ`8$Ad?NFbTxa_g(pj@=`g7Tel;6r_)3ZjQ1Qb&ak_9$~4?s)<bLWhOCXBG+GEWXyjKLAd1aJ*y%!|<T4^@3L'
    'cI|L|)oAx~4=ob50RPXaF>}LteHE6)8t5U&GxjvP+xE`#b$Pw2_~S9#pNI9jnNZHs@grxS*!bjYvR|_}95mSF(f3N-19G8&_dwDasEJ3iVHf=MhhPd#*qbi>'
    'EuHH_8!APnJvjjy^GbLS`%auytJM8@PT>874VsC)zIvYxfjn8nfD#~!K04W?$BY=vcW<#4KBu1VfY7km-*6sdUPuvs_Bd8Yf5HD<w&`aP>m9Q0oAL{ta~;pb'
    'GVV*XBUCR+jV;B&1?3xha<NvPM&}B|d7*GUX#cdY>w8|Z4-Iv$xbJM{X-0V&!{M3)5eH!(pf&){zXu_E0yf6CCye#k<{&s++=t`x1>f^L_paNbB(tyxN=JDV'
    's)y&3&uZd0kj!4pCDa_ZMxNtui0K@{UJ`IqSOG1X4*PplC_&Q*;#%PR_l+3<?jWz7#vj3W2LKd>W-f&pYO0t=#w$2}%D!b?hivb_H}_aWn@YS{Vqm$VP&N?3'
    'SowaxO~o|6dDz%ZxsT`zP(Fz<P3HLHn#mY{36$PlO$M@7_>kYb*BW!S`%7d-##nN#OXcUjTX$q)5*g2uC=U})U;7M)X}W%M5dMjnp99-t-I$QFX}TWc<ZYOg'
    'aFp|{AtW7xrlWD;8U1nN=hPYjqHS$gaQ!-Ga{VTC=KJij5=qv%aUh~+yd}m+ts&-v7q-t*JU^aF<ik~GAo`U<Vkr@eh?poxc*ROE13}p}*rXo|5x;DZytPWr'
    'yW+ic?tEm6Xg`l4z?KSMcgN55V*&M%>AF$?zhCTCBuWAw=UIH8|Lx-LoP0Gl+q>Y|+>f8Lm!H4pKMTd5H$RI%3-ihBXEDhAEWEx>eu)1Ie&lmOe*WV}Zu)XF'
    '`7sF!lOKg}9u|UM#UE1*o=xKU>-02DUVjTd+r!lUuo6RW6NLHHH^QMhg$A1MP8`R;HE;2)mjmn*M)yx^EJ8T(F&o|fL)a`#7eNloYgJ0ZEoc1vIXxKA*!4hv'
    '-wtKX-xtmAi2J^42r6tB<fhZ`Q%Z43*y-C#6mCf0P(4Gh8&!l4*r;?XVY8oW^r2q`kM97=7~{Fpc;Br2uvud~+!B-=Ke6qgk0*g;27NXXR%t#*KNk)Nx`<{T'
    '$_AX;g%SclIu8roqkYIKo9!KZxdXX1c^vgz$=)hyHo)bXR70n~rM|a#ee;l>f#>w{HtZ!V=@t5f<vJ{Dt{gV~=HGNlZ`8J*yC9*qYP_&Nx=*ge-r{y~Aj#tB'
    '0o}h3((L7Tz8B+pzYLyZ*^2}Fh0KF6e>?0$h(b<rn3($n*TWdgoH7kmLM_*60o<vmOuD7Tl`aoG>_&M?Q8jB15DG(oL{4%L)oTalQU-IaxR&=4_9P+5$O%XK'
    '=Ql3w&eWjurk(GhwDutN2Trr$kNwM9xGmUwsGmueRZ;&Y2;p<_sCU%R`^rA1?<pQDAIChUPCXp+_+72V&ZUOiEokL<xrfB2!1N!(7-u@A$Plgi?x`Q}zbW--'
    'Udsr>!lzzChs+{0@${p+_i)pea1G%WK49oyk69<Q(x?ogs4);><S4g$>Ld@pM2P?P(hD@~zT60r@2voK4Y^=7|Lmyk(JEn$XXMIc{#h{=ehz!G^^@ws<pox$'
    'oTq*+)*P*<fr;0*J`ep&>xcb}`90u%W{Nf|#gn?urV&gSz65&MA|#yD*9aeG&M}Km8UC&zlje8mIwsuAzn*|cQ0z5;l!)hC^bLJ_nU=wQSOK_KaW@nHg)y8|'
    'pJnRfLMi01Q1;_4APo|`6nGvFk86O!qkX!|S*-z_I&}E-h5d+&+8w<!AY-={ZxKA>hq^yPvjG<Lp5WRLt_o=S_G(e=y$@?(D`+-fPxc~G50`d}n>J*fG=ev)'
    'INiT)=|svDqWL?xc7b$k?n8dxSlUI_9nqihas_4dd0X$qtwJ^HdD9&F)B`BfrVE)iNoz;5Ew%5#>)V2d(wwi=T<q06c-{p#utorh|IQp(WtStQidC39s|I_z'
    '9%yd(cr{3~m(tkc9-2>lEB4Z1dftAv^2o;%JSPX_Zf>2HlTjSbnx~NeyZW5gqWuK4sfk|-LYbXkw~y~fc8GL!EBoW9SHx5e>e%-Kcg#MRds}_xyFLHn?B9qr'
    'RTO)-cY#-qd#Hc-N<zGMqXWX5dJHX==YKEw1Y1q|OZC0=x6JohHThg5enQC~EbR?x_(AVCmO@(P+rOG?NR=H|X$=^~foQK85$YJ%{LmrvQF0;6p7)#A)Ald^'
    '_r4kL0J66q*B-)_e2&<8UTTNJcWY)q9(X^?o=@tVOg)QzUEy~kCk}i^j|Fk8YJaDg%<}4IkH3=|7pvR_=YCT|2jbSC9k(bYVA*JFRT8$cRc?s0x3L-bf|#KV'
    '2-%pP0)$-RcmsC>=}PW}k6Qp9+1Bm$N<LKJOz4U@t`j;XgibFiRp8m+{ikn%18pib2KR$Kw1zzA_hA>b0P_QEu|j`oje4WuQ~%Z)W1D-F=g^~}=V+O_W5fOS'
    '7GG!FDme3do4VHT_vEViq*%>bj}8P=pW7Gtlf_!X*hIn^Y>r@xD`dJ*tRLi^*0I3_D5K2*pyMw&E|X0u0Io+1u2A+veoXES;?zXn2V-(iISF{(Z!Yk(r`tj$'
    '2dRRJIk3hgr1rTD>Vz`K)zNJgh4u$=4rJ@;$@%l}_No%LZM%=U^kzW(viW99;MX-T*=={7w1x@rf<t_;mob3w-G|+%(dZ@#L4T_nUhp);^|JG<6K+@3F@6fd'
    'O({3?`GA`xZ}48apVjHPG`FOLrV@7;m5!lB2)-xhh|eBtj|yMwSIqEC$x%`S9lY@gIJA=Y@h}W#ce&%^-R+@Og;E>&92^(Dzn~8eEuS9F)f99VA*CE&G-h=X'
    'UkvUm{lUoxDP!9q&%(6k>pSw5U|e#PCAF}=DptCKZhw>$;njWg7~LmNptG4C)A90Ucob*TAe69T&sl##mnq}jYnFEGLO$c;3D)00>-o}oXmL3=%fL6nJa{9{'
    'gWy>?b>Yao2dTx(fH=6?uqmBf@D(LDxv<Z{WEdc?i%i$Is$y^T1LYHt#?ww}ON+S!9f2aizTa_u^kDB}={51$=@cA;;j+FKB0v1e(pEs;<P5td`NU8w{5n{-'
    'tEoUxVy9Gzac~OO|8`M#;%i$2t<{AG@)2EIvq~!6vR<>w*58qxZ`Cqwa%i@HUDTbBd6e{-p<&hzv|2-ojt10W^*ufDOs!FS-^lX;+Y}%rFG~_biW^Wn8`@bs'
    'aCx)c!}JU6@+<py4!5_|EHK*o>1=gd`tg4vl3E3*E6+N8sKw-Itvo<Ji1MJv9h6U@)FI2PW2GYzs51ETiOz#oEbg&zV`-~oWVa!#9Xf}%Y))O@xkWK0<!_N@'
    'L*-6yx7PSY#H9prJd{P8zv+B#RbOkAm%4i2)Ca)&gSbW%TJ23tc>&`OEwI98?t;HHcS}ePhuyWg^^fsrW>G_kH3k|4+mW`L`xGyq=UxnjH+@BS|KeQxTUsk`'
    '#QjwvS_{SVEz8Sj?=Ep$H&f$CkkK#4wCUv#XYM&Fb*5TK`TD-|T#gys+ZkIvw<JX76gX{cX`8LTTMVXElUq*spLZN*Zwy)ha~;a|xwnAf1|qJlxTW%y2llGs'
    'AADXc7qf0X>d@NlKa19>q~HJmMT49rwp>t2Tm4N^P-N*{0sIu;^wcUMb<d)A^5X`;9}4eqzM#FpW1mdwYx3b7P9p6yW~@zw;F<hoxJD3efPbI&lwT^><i#?$'
    '!_9<ceGxMlYklxuk8l6x@;r~him4yaKLwq3)cBbQ8~W70|Ixm?y?9TGjd4AMSKP~&UtdEmSor?t+ST(YIp?{p(DQxRBj%b=P_PlT9cTN)8GeO*rUmO~@cRhP'
    '|D{iyvMrMTuOE<)igUEe;)2ccjqk<!+COt1)Y`{YwQiJOtaQ_vcT=A8mX1=UKch7TUQhlOc!c>WKHhDL=ho)M7qLzaWAVMiF;Bm*gfh3M;-?ES8QMcW2d{~5'
    'tg&hh`JUzP&MCp7pk@1Ms5FzV&XqqBwaTaP)wk-bv11R07F+EXHYaVGyB2egsAE~Z=e1tS%2{OUfjDak&M1llphgdQWNIt0<0ORQU*zXow0mxgH$5*x_6SA>'
    'b%43Kj?CX%{Hnm6o}KH9Ro%FT@TxD)-gnDh6C_BjQTV>}dCqPtBJ-qX`&rktmTMj4@8pD>JtZNf9V2g;X}?tKIInkWe4a)~k#ic``^?ltamG`w|D`zF8kk>n'
    'HOVgPLj2xdF=Gye1CjaU^6g(Oth-{ZTyvaK&Kll<@}DN=rV<*oGy~30mVU7h2H&9?qeqv>p}jHuqJ3I7`wztx(zZcs4b`LSTZZ7yB~R#+m}jWv@tQ}Rim4Z5'
    '=2V0oGw>tkzQ3F+mF?-kFAjN#Nsp;B#P7MDczug@5`j(1y;KKot<U66q?UN15F~%UN*WL<|8jZE#hN(+rUq~%@4$tx+MZ-yh+uLt(UVx;<yOg+*0u+qjd-U|'
    '*y^{&{N`rL?W*(d)liq?av;{*vDnI6&$3{eE~If{8lDflCqnZ=sO>>KtKZGh{ry(!aZDP^7F=0ltz?Ug922V50HnPC-)_Cycq4V5bc{IhA(=_aL~nda7%Ed!'
    '$!ue>G?9JR>Nz#8aw#1*SuMFG-9mF0(7$8!efHyi4rrgmSj-@w2{o|Y6|IeKeQ<8rOV>t*?wnk(kzv-{IJp39l@M##L(Aklw>)-}-h<nzkhV~M@4vW}3n<-g'
    '-8spOE0j-Hw((AMHm2Um+{-rncpW5D)Dx&HjYgw&JS#r^y|t<QG~c>(Ysq?V(^@+$nt;=B)|Bw6V&Na`5tD<hsoDFSbApR`na*EDh1B!3LYJj}(!<}Bv)6c@'
    'X8H0t=HSsXXc%0ZhHLfWb+z40=0vAJxo%kxVs7tmoRQ>bz<7a<_sggY)Vy1$g-^d@|Fdx{-?Hb=!!exEb80yrY#rK^-`45NIJ<qVB+IAae<NRmDPAc@(Gc+h'
    '#Qh86b4Ln<>ATii%A{>wK8<8-(f~;Q+l$hK#MVzqwWF>->Y+}|X;ynEyuesvJN)x|-B&yKVr@Nd8sOV+E&OmfGyWr;E@*8$X&jZt#(}naaV6#q=y#Mhg}R5M'
    'aZdV)M*9Hb5(%dOKJiy{4jJ=+6l;tlA?z^wc;avM?x1o{f6K*KcdIqT`+_bXQXHy#i}5C0e!`k4KvmHXGWET_jhmA1#IeK+YD5*2*@^JikBYW<uz1G&F3_DO'
    'KSW%^Ys7;&OXol&wg%(&8MY#I5RZfY;5irf2T`w^13&Pn><R0TgnDjf6Lk)uP8n#7Xq)mh+GoVYjgJ}BJxBHUaczyQ`u(VPh+k^m_X%O!+|L;}J7`JP_UbXg'
    '+Gr-dnu%X_uj$UKx^5(WBSX(ku6OVSY0lKyMs1tsbFqGleLw4OqmiHff$3ftQ&3sYuw9SF+&Rxum^YxlftveE+18n|S_c&kw6#}GU^f6OL_SnQ;`BbTmj&AS'
    'L7N;k#88v!^fJ2j*lp?W8~C!SY{vtiooGt{6V~KhHsD)laRB*hqRLq<E1dzfszQq=Vym|1{%kU(Mf#nzrmoq3JMx@obS7%MW$Y8gp0y|+BHn@XgGjd$F*YUj'
    'KD(@XpHcHtc9gVCmQYut)>mEA%gVjDpX1!<@sH(O`h_SbY~xy-RY2LR^_oWQn|itaH__Q->BaLJBkVl+^upJ$<{09%^|M#sq#g%1V269c_v#{sHV5YyXg>-c'
    's<VBL8`HLncVY~_&UY<=9*gxo5AmedewP{`bk9(n`G7&D>jUk8I0IK>qDT^JyK=(TX?&LJ?rq^iB!*t^krXywjt`u5gwOZmol}0ZU-OIl4bEA0sZ9R|v;t$e'
    'F17JREAcEK(*X6VB~T}d^M<hDg~sO$&tbEVjWv<r+<=<i33CS0R-O^tJ<P=!^%-l992$H%|5?5f=E^eTg>Rm@9rROXVobnSDVg!DsoSQsWX(K?M-v&>gJ+&#'
    'ZEcu_*Lruv#K*K2#d|>ReC6{~TpI`&hglW0@1nDP78u&{;4}05jLWNc{8{y^{Kk4A-9H!)^*(Zph;}nrUW?~e`2{~7@UEW=9(qxC&>IwM!VftR<(u#a0`jq*'
    'h8NE)_USUts@{>GpLJbo?NIjncybi7o~GukH3VDIcq3fy!*aZigkN@CKMkpu3+C^6*j-7VvQ^&6o**ApH21j4cd5TWgKb(rkS<le0zES)aW;ix!W+kqKl=vl'
    '?v%?1`$n}4Up+s4FRhsb-<|YW)AMBX9YG$h5`yHw9?xX1#Y63|>C-`m7Unp3hi8ijlkpz0o_jrhmvM{kv2p!>#M^%9lU$x(zgD@M>*Ck~@ulCiDVg=NvTZLj'
    '>StA{^;<e%+q1-q#_5=yFlo7~{h7>jea$rSC;1#iMXPgxbzd)(!hMLfV7!ane_{C5A<f_K`KJF?f0Wh$JxdZAeFQ{HK$v4M#)oJ%H(cMuzTrUP!f)um2cmN$'
    '%9a~?573Ve{~il__?SR{8|9lv0cdzf0nwWmgV``yJ&18=t(PK>%%$fuH4o@#zH`3iopO($e|;p{7>*Bs3K;F`T_B(TD%-}_mHydhYY2XUwh#3cUEj>zQja_0'
    'Xa<bQCZAXMi*8R{^VFD5lH-T84XC}?3mz~Y>Z6U|=LH;d_Y-mmNG&C4cZkB)cg9c*2XuM=Ag1u=61?^!E6>Y#8^R6S&w}|M982YYLd|Sg$vyR=j+jHrANbDt'
    'a;9GJJM^Z&8UM}ul`r1!f^VT?JisN1HCAh*{T#|Q2faQJ`$Qqf)ww;{tsM47))S)AZ4GEGQ{H2M@r7K^a7IkPUc{C>f_m31KkqUHG}@qzSnC1Cjdb61U!w5U'
    '$)910L83U8Sad$4q?RtMeWHQ7#_xg8v(JMy!gNeYadFzFX6|p$ag}=<*SqlR_cP8#fj&0;cm7o!mvPu!|8RH|Jo#ybyF@&o#`u+WKZa2k!McpB-+TH|3>=Ui'
    '8|ooY_<{?yiapYhtGsFjZA3_R*zX(5XZdcta1C~cp3B85E}iG4m|dPbmvi;PCaycG-eluyr?D^I;GB_(zuP+X5QQPEd!FInKQ9I?yxC*Wr{1)TxG@{+b<r=Y'
    '|3nI-btr7eQeI<srJtC&_OHM<Ygs&^zx7~jDb{@Ir9Qh&<K2wd4nG6B%H7>`(=9>|K2qe!-^=_oVlk-a?wc4xpMLpryaCEeCohn~;@90iLZb80H96gL4nD*%'
    'ggz4^cW&1`e0}ctogUl9yRCnW<b4ggXjubCEELL?)VK0;**@TxxDBQ&a_c9EhZZq$@m#M`-l<-L+Sfk&tT6p#;J47S3P!Dm&+X3kQl9@X8$E1WMvfBVX!xDm'
    '>tbIA>omF^Z#)lR^MT$E`Y*1UCzk(!&wFeX{@%ML)(=2e1#5!uW%Rh77U_LU&wO3|GUZPjwWT4Qt<IR~nrc4~{(mu;3>y$H-C!TS@O1zu{tUS=^i!9)$?!Oz'
    'jT(D#_TuyZv6{sO-t#yi`ehMoM|;HG(tV|0pkj9CJN~~ztwq+-M)^w4cxJaoVdiHZx>2JBG@ibGJ_m0?HX{kS?zFfZXKqR-XZ$rDH<^`sQMb`w&E}olDsI(E'
    'r%|5g<1LWSZPbyDlIks#b7j_*<US%irf)B3{vH21J)(r27)#)$*~(au?iFIcF4!2{cd2K~;J7fP^@#FJ{@j&y^EG0m3XK4KV;kQtY(3;`MkVAy`a(wFr-1>x'
    'NsX<fu`85--ck6b5Mx6hQvIg=271b3?1}5*#yhs7oaGalJVmMT5oedr-Vo2l@~g3vn$gH%CT)e?b1&lS6HoyLZ!bMbFKBQlWn*2l_{O&Oyx<<2lRpDkUa`l2'
    'le>w}Ac~iI#|$ou<Cl4i@6>`QSA2P$DN01Fg&hMWiP_J^BHnl-{)8`jD$KLQrdzjUI<xwAspsH}aTPX^^N_o%`#bWhZ}mApBTn>Ptb=x_Vt_At@Rhs`Q6b0u'
    'RaEI#9_F1$++k20^akN0=8g5Wp=QiJ_90DAzcKhTQvK#h_z(6yVv}C-SR=cU?|0cAM4`u~TRz?xF+Pi}+b1aY|JLsp5?;h}#@e+;m_y{SXh@3pi?t=mG1LM{'
    'f^6Aj*EVC_19aeJeVP7?H809#!X8HZPd1s(RvEk7pQG>M=Y*g0%yt`}=bzYXLxQYvXVkGi`B_!|8R$NVqmpvKMw~8vWypO{9buFE|6Sg|SyRMCHi~w@v1#x*'
    'hJ;=GugWiI`{l_68#XMc3|2GZ)j)sgVfQo=wrB&g96)owv(6!u@yU;|?hKznObl_v?Je04&MUQCE<Rz~^m)(-eKCrG4P&3scgA~SoT|jVdU0kPD@cD=`stZ*'
    'iS)s*#64cZYz<Img>d=6)i>VRlD=87hm1F@?|A)e16xIStGht@=ikR+88M9X{V(IM<n>+uS;r1yJ#bG=L;KV{RUh#@Lq@z}-0OO5Rq8tw%OqZUW{3%dA6UP6'
    'HTs3cd3vQ^ad3U08<UH1?qIpDz{^Tm2atktVkL_!>W}vvJ^6Z$n19mFKQ6)Vm`|$^?*I-WC#88mr>7#;w!cv$jiy+8TqZ7G;l$vFtOMP&h=G8=kMe29M2s2a'
    '0CLRv`CBzc;UgV%oy6vEV@an!;WK3CHa0d6*2I&*8q4x?bX`2f^P9zKMe!uYP`<ta<cwikN%afNCx~$q_ZD-D_~F?8Tq^G%`I#_QMf>N){ZUK<WB(o78d1HP'
    '_kxObfCB{R$@k*i5${!B5Rc+AfnKJ6l1FFX=L(qyM}nATD_Pds=5zH)Iep0k0=T&@_pw~a9gF=ep9y%FP3}Qn)BAdg{|_Zd%p8H@iAK&Tg*(^3bJyb4wg>!;'
    '!he1a<vE|sS>{Z{4~@AQTV`K>bxr>`=MaKmvd%oSAG;!290XwrBD{VgeDvIpi+QqFBZnh=cklNd|Ne<E5JjN&?+oJ7U9VSqpTT?7AeVWB9`!`$<ts6A;n0}G'
    '^M8=zf<4^g+;S#Q>%TpRm714=IBPhE3pN_&$eNQ;XOJM4Y?SL(q9hdKRp0$itku1bhNIj=1J4DJULqNuy;$dn6ULtNw|ZcYvBmp-ftO4^`<FQ?|5`WJxie3h'
    'V`%u#j_kzJxOWz>UC2CZU(PSehxvHH(mt^q1A~*n=uMF0Kgx|uy}=PVyrh)k)99<5+p2kOe#UnnOLBPiF3t^-%jUWr$Y_AvEciBnC*8c1OH1zGK-p!+U#U9I'
    '{Br#U`*$!m#rrlr4zr%H6^~+_kUV2Tc}m0)h;=nBAMs~#jqrDUUAz7O*Zji&O>yX1wSYG`(}a(M{*uKT2ItsNtb_8u#phM_1-a7=$-GPbvN&IdFM7#oe8t?D'
    '{h1D5JJ>W!Qq%6^QoaVxGxq07EZOdge%_1qmHMgFBhClt38a2EmZX>Q?qX1>6PB(9`_;fYlV1uL7r;jalBXN{s3G4nWS+gn#TXxCV=>R?_{g~zv42#)&gL#g'
    'u8Ti{{rhw0+aLGo9pA)T15vpsw57|;UNd~=A)G^)t3sKo*7dm%)L$Wq*CoWqrtSaMJ)4vh{m$8B#10vCT)L+8|A-SBd$q*1W8yfa^<aJOTfKV5p7hmLvd5qX'
    'Ht`!hmMhMAt6WxqXG15q`ea@Ku~tQlT0;&MF+a)K$UmmIcaS4ZtjPiLNGYs<)@$j(xeNPA*d*Z(x_i*+l{WFdQf~;)AQewjjU?l|$*iMMzxW39WJ(6Vo*k=j'
    'FVhmR#UPJ3$?wDGb@(~q_tG^ec~;c-RvgAS4C+It-uk9f2fCb_{Rj9VCzk;{Ig`zbbUz?o#`zIz5c1^Q7wz^1X$ke{?9WZu?}EA<wPcR(Eqsa@=0)+^z@Z47'
    'Nhj!+A)Gs|ZE@`x^lv7(mJD2s_)M{>z6*aB^BMcF7Pg$CJkL`xv3D+fxg{;XCjZ{#XGMG*<1?U!%l)|T(^|I(HfI9y&&G?_c|#o`?C1HxIG}N&ah*5@?0bu|'
    'g!;o4!bY!2%jkX)K~7Bgu+USlHHKD7N|@_(us7dfoLk;!(AW|ki(#*omPv>GZOE&^Gr<{4YlFBmlyutL9(h`U?_O&|QSKRZHiSP0nqwbd_mZmXy7oY6J}H@V'
    '>s&q(cF^XYGIgk=-1&Y|NanUYq*I5frR>E)ygzdGA=k>6dm)*$D`4A6if?q;mj=#EG5!!Q2bTOL_IHBJdVI8g|1`q-s`a}j;7Rxy@yr8hUgE(!y%6tadcP3w'
    '{l{}*wn85T;)VP%kGaEja>jKGwt>d{llLI4b^7iWG~Pf{YZUylxe`FPC;<8Mqm=f{O-{@Ycaginj&%mBsMM__^KP^pbfd;&XK<W%a>q(%P>cp;>P0f<Sk|7M'
    'u{jVonuwR(F=>@eS>>I>G13j~{H)WD%a||MhEsjFNWIv#wpXi0$u~w)F5GwCxLkI@r^I7=a>{JejJ)d?Tm}vXQC@#(^A}lp+;8wTNF1c>F_u`o(sB$u+rqw#'
    '@Al}glawRqrlE!$<W?k)^O|{MGM_ygUu@zzwDuZ3lNYy`=|}nMg#T1m#meDu%t!r%2h)czzvDUAkQ)VKf5xc-9*}dpRP&mTa{BMRK>yvt(eIvV-uMptD2vE{'
    'ajoy<V7vW&EQp(}i0g{8?YsTLZ+qSMZC=O6;hWMebZ!3yJZD;K!K>lNxZyn1j-^eOJmW3qMOQW+wd0Dyx2}h}2Xd8#8AhFg?7F?Qq1<P2yS@6HwKQSYZ1eMa'
    'ti-jUr*vg%pow?hO>0U&g|&B3Tjl1vc2mTgJL1!Y&uI>x6h?w|c!uW^<nD;`HOYDNIuV}J4+oHO?dX{QgZvnJr|~d3%*smM9<U{eSj))aE9cIsgKB;((5S4H'
    '=5Bt-ypH6N&&(MihdkC3-^E!ku7?<Q#eeeJQ@Hk}UB54VNPkRA0hbET*t=hZI_^pM7~tEIChK?S1J5wfXQXppo+sM(89>g`@)`b3^9i<zxcJu(g)#IaUh8L2'
    'Y)<1}3FqibN8#LwIWZpL{&BVozhr;IH)3a&Fz;D&2eoV9zFJO*8=d_;rO}e&$&>rch<nv~HGBriYrEBJ*s>XK^-6aOzBl|2;tXeP;o6jVrwRBe6|t5#z<*JB'
    'BmQH;$BT9JF5Z{$GqvugSTlb^Zfu-~+)w+%#*kF<9A9Q?%{-4vht^QV2k-j%xvsFdRGYx-@0BgO>{rYE@vip=`qyg_Grd!9TjemVghpJUY#RH{4@<y#r?gkh'
    'IQ1?^@hQg2hR+Wgq_~DkYseuHmj?C;ntHX~++s5ef9?8}xV~?<l%MkID_)AXF$cicM2=AY%<dX)HR!hh9hTR1idT)!9uo(p?4!?DrZdf|vwb5EF28@(bs^WS'
    'akHI4B`ghA5A|L&uK2n1vijBOe55llWX_T05~ayuo`kKOVlO>tV3oALR-Iu!*YUls;RO86eARdO$gl9NGB|a<AIm`gLD@^W|Cm$lh;s0+J)GkuVj#q`Lmw~g'
    'd&>Ju8_<eNk5~(cVV?niH^(tz&aAvg1<#<fn47opz>S;4IiO=gsS~$%&ptb6Kz!WVS2E7#yFhX{f8pDu>LqKen^%(?JWov>+S+UhJYQfx#l2+yQ*l`;J9@X}'
    'eCuH`%U2M)Hbx9z_xuQBR0SN~0dg{cG4T9N?AyEc;j+I19G9gSd1;8B=Lgsi@F%tYIMm<pxpLD@f^$n;N~NcPledB#Eo-6^2dFq#T%Sehq1@g6dK%GKD4h5C'
    '5I){L=h}C-I$tk7=f309UG6MC^IaasC2(^?FCpsmz?hax!j_eUpK<%+;jMFMW6VkSkh;qCny}6JgzW_2(m{v)7BS83^X=g`Y`{Owr~mKaM<w`7r~$!ip4N`='
    '9YKp7d>O#{3%f`80>(fApS8~)xE#x<^WMhcA<me1l|fem>(XoB@tlbmN{l0nosaMHZKp7YEvoCDD2IZ*Yhq74%*EO)K!0<#PDx|`!kAOFrrvglAu(_qGJlX@'
    'jx32D`>W)!duoJV@T91^iG0V_2(HuMUVr)b(p>&s>@~2P$fs+pr4`ud3GfW|jtbvGJk+2GR|MX)PvO&iybQrlc~3n*>ytLW)T*q_wvb;G{w(w&X7%vE+BR%T'
    'GB<NRkV9=|>LgkBQtM5evsG~xoyQk7)VinF{`N~PE9r|E`}<N1sr>Vc`fZv0Lh?ERVJAlQcd1*IJ;(dMnj1OK2Zb4Yj2rj;T(JT<DSZvbHE*Kaa}WxDXS!|r'
    'I3mlLd48EcX<pR(VhuX2IadPziu{+iug4r~X?e`eK|YwiC*ETQwG*;6af|d5pik`FzT%pu-MX+2(FeqHqIw~hb;bN?+!*lJ1ZZnnqr{IN3mTO{E{udv3v>1j'
    'jWz9~jtaaE-jKUPt|80bM7c^Mx)=5)2z%Y=;VFt5cio=TPjPu~p?{Rs^Y7#s4O<hS-cxH0It1c;$`^Ofr%dh{(jC>;`t{Vmy}77KCH;M=dwQ=mjeK=B@Eye*'
    'eTa3r1ugE!lhh3tJ{+D^tZ^^=cHrH?Jy3<*q?>=WY|0LF?x$iMjD>$U8A49e?Q1)l@8#5Ww0^Fk*J#y@Ta`vI*}#32wyvWkZE9r8Ylzr!)aLs1Rk%mBrdsL@'
    '0UpZ`I2<#3rw6O3-w&c*CpTd3`Q!{71(GL(@4rvYQLmIBzg@Xe?%#)ZJ>&@fdJm}mRte==4>9L1&qvL_e^XB$2K}Ja!Je0LLu!F>Z%h4VHtN2O;M_Z*f1ok3'
    'RFh&Gl!Tc&vv!`*hz0%|<IesukGiF^r8&JTnn%`G=WSIh(1#Cwai1?|4=>9f*7Wh(c$<$<`c^jH&R3g>f2)qh-$y-B>isU1J|Wjjb^2f@_A2%Y{0{cxsUJ{$'
    '!J32O^Y5e~^L6glnNJr3q4W~5rkp42##Q(Z$)TLiZ<+JK`N`r2?unnNNzOvVoTT$&85j;X-fU)n_rg61GbZ1MImypX?k$-J|Lzs@xWJ`a%zdl(MCafoegtyL'
    'JDGn%AA6mgCc{*nkF;`yf6R4CmHWIRfAs(PJ|@6CKId2eHCV^;@;|93>Y5%cvtFvOhb|8D?WOlk{AGrJc)33-^LeRfD-+ZCEiA2z``mq_XRwVq9)+2No)_rh'
    'U=BWd9_HfX_o_j@xN^c=<){+8khX`$Hmlsax1SI-M1KeW@r`%tNvw(bzUtkP_A-sh&~=cU^f45^0(}hRT4;YFZok(4G&pw+90rYB3Y+*(&ZqD9^~vX<`WflQ'
    '!W@bCOUx<c1x7RQ50bRr<nsdh?9euzXirQ}zce4Q!Aa%R{dA6?5XHh4eNvx#5!b62qmAS`?VK?FJ7wT7)4ZZ_?p<p43cnjL@4x|*8-uRtUEuisrN-cX0M}oE'
    '|MN65V}bU^;6CYedq?s=&H?2}(*BW*@jqnU2yMAo1MO@+G<<)UJAAIPp9A%eYMPMFk->>pS<P_Xk%t0ftrzNng7ek%zhDf5Y2JsU!I%y3jvr&pr&@SUv~_80'
    'Ec*twe-7H}OX?R#{z2fmE@o@Wq<c1~R$%8nsn%Nm4gD7}F38nhQa%gfRoQ+Kr+xxwg<IE246MinZn>h&J3IAQ(ED`sN7tEk+Z4AB)(x`f!;=qaF9vIjv)?f9'
    'Sd|)wOYpC-_BICpZ3DSKz`RDFDF(hn09u^F25;eK6C+AzDA@LgB#p%-T>_Z@EMLjQTu@vI^{oN+T#2}nbM)z2pX+0KvB-=OrWgYFkx_dm5Plh9OVDSn<is<A'
    'Ml5LMDO?`-o2>JL7-d-7&ztD{+B0&o>qLC+hsXY6-C6jk<m-Qlp`>&E&&Mvm>374J0Ux`#R<V_e0nitRUPY+?0+0HcxKI9>oisS|eeWXdzRV*A$lU{H1@`}n'
    '`D$hRQLauS_!OLbQ|B0CL!)ymFS)M&Tb*gh(}R3J0dfJZP!ryNc8PfrG3KToI?vSUMWsrsO58$E1JX<I@p}@!p~yLrBKCMa^>YW0fAu-3Qv9ptnV;?J_{OPw'
    '`ZbI8<MI)D)xK@dBV(R1hA{a+Yte_vyCm!#=ElCzZTQxjG}hT3^Z_02txv)!J!9_)#^L51pEAzCnmymZ5!3{}Z{X<wJ&HkF+Dxx4fLz~8H7o$`Uh1CcHHLZ?'
    'zzLkpcu$See$Z9>_!^x%niJ|zc_H53GuSlE-{5e1*XTN5_e+_%0h5bK=l^7lNb0ItdCjWP|4v*#SJpNC^#<g7^7P|K?ol(3$oTEN^>@}q_J0?-EB?kjP3rGa'
    '=;q11{q^R!T<jG9{V3fp^thq<5H?QxLBLaGa8qXGCtR*d;r0?P7jVt7#${9QDb=ZkUEFXl>PJWXdo~QfN9mxw9M!kbC;MCwK6DNJo^TX|E3r@JBN_MCC>Jw@'
    'p4}?UySxVWLA^0|@?N~(=gUxh?wm7pI_u<@5N{jl&*<K(*uaXtoBeSnmqKeOd^+fVF}_Dwi(ome-P+$X^r7V(%CTszE2}a3hO+=Ud4wO_0^2hxeZo2MIw~bk'
    '5#oBFH-G&s23)M+p4h5`E3dWVGnM$khaH=1?b0}cj#4K65o}^|821D6b+3CCtP!?Gn%|*S>lUY=e+>I#)G^*MS7^W89<IDD@<v>F-E&)>TaeQzAV%%L>+XZ^'
    'gX5rctm#@m<`3{QMbtNjHTu*uxJNu3&43x2m6wPc73XOn*3!9I!Ts2$S^?mSW@lyJ;n{`X!}k&MxZ1xUuBVuL>eq$M6LF@n{o;&0Kj)L3@&PV!Gu+*pKAok{'
    'D#oF0{4DQF{o^!N;+#$M>ziCtnhWrM8@?;lc&*=$fwQzt+SE&PTnyh1eEeU;=~-xh+T4miKJfQqkGIODD#T)as=IFO@lj_u<l8^%viC9mWz6$FMjkueC#J6L'
    '^Zz|-5&e+Ylh(Zk`$Yb2kkmK1O#XVKSHv0vt!?DfbrFB_?+4oNuC2_%E?{1O)>(Eg0Am%@ul_yVl6!@LG<s_=W)$l~_(y2_7S8>Qyk~v)-LYd_ql=rNy`G&L'
    '*z<U%ut@xSmn-Kx^P=%Qz0`Si3A6OoGh_Y{HN`LDj57Ck$(N4-eB!Lu75?`U^`edZ4E5{kvn{^B9jszIYd;!ix>#@UUYb|s%e9!cv#{TcaUwtDEf>~}{w1-u'
    'RqgdcZ&b$fX7wAZ`=7_zdW0J5UKP(N-s2UXrDt==e0gT6>wvBiYUPCOSlHOjLomg@kB7IH;VNXz+|KW}-kzNJl<UvhSE8KAK9k=$%`4OZPCvNkqRqWzph-H0'
    '-jVXs#+2NLeYOev!3NHT<X!#EiOU9N+?`YSjSPGj=2T<rJc_Z8Ld1;^fV1)T*FudYYc1I4hrv~A@Upjt4Y>Z_gqb@o;ml?13H63l<#Qh;CpF?cpGF2Q&f_;2'
    '<M%YkNj#2aQ?p}v=qs?X7tHyDy%q+>B$a2{LH-jkW6*aipWU684&?W$(Q4qC8?0r~^L@zLfWJQ!F)xeeIkyn!5@@FJGZkgt#o)zhh}iM@{SkPw7;7PHDfmZw'
    '=-I2s$)we<9{{sGa=81zS7=tTFG$#Gz#O!<4p*It1A?=9iTy=yJATGu*v#OAYA?3U@*4W%!#Q%=N8$XbpNmDzgm|w}@JrblnO_+c(06+8j+USgh@Zn=*TsMQ'
    '$G`rM|M9Q=@+Duq`PcvTKmPSP&E4b*)7)$B=B6;2PhVeu{>cBFPjiJrZW4sAKZD>VKYjTb-pr=C;_FQOH7tZT^Vuwy`#GBy|MmZf%e}wsSO4p;kJpda|8sW|'
    'SN*^LHF?>;-u!?Y)KB-Hv(dw;m2ZF~oiHQMi)k&%y;Q@G_F)^><3{DR21n|{4q5?b2)Pkv6^`v1P!2{3r(g&!uukB}&{8A39CWKjpy-<Pu~!n;I~H5wZuP~E'
    'q{{$0zCpJc=mHi}b`Z*<km8bbb7JU~9JSbzMdN2Qo5s(lac8HfxhI8oQ(e<u+D)dlL}(g8lr^O{ZvPR&?)C(~i{M>z&%Fb|nMXT-iOOqWi=r?gu|bK6AzOLv'
    'lU|zY_cW+-AcZhJ@VHcMnp>tF#x$~i#$*HV1K|oo$`q(G2?`p83{Qjh0-2^cV9PmCtXs6;d#9zYY14fgv>j@0QfcFT225)&X+&PR4am6O-b%&G_oi~r+zvI='
    'n3|!J{hS4I`Hjz-;laLcg@A_z(wSguW0Fj9Ya_f4XvYGO@uAWZUGp(64d5OFMNz8KJ2zxvSBqBv2en|x)Sl5=E!+6898vzYsBd^b#kZfr<l{VuH3d{Fr>4I4'
    'u3n}#E236hsgw_xEfJp6v-XyB{r+}=W$HlD#;df{BRoT`?rFs?<?*;i2~#qg(C#DL+%3Q}#PvyTxXky!wJ)g*Fs%vCk%QKYCa$G$1#4B9YiYujTSCw&I?@#7'
    'P9D<g!E%J{TatPXDQS-cS#drOX@9&oyvscvZ#q>AlBH?*IFz3pssx>`i%FW>c&8h?RFNT{g<~^NYiL$meJXp2P|WcfXt7iXtk(jKcSo%^mTAP6-NWQ;v}ws_'
    'y4x<$weY|^Ib=rYw6Q+7Tpr<j&vvuwV$p?i+<>Ten>8yuUbIfPGTe?~eKxCHS{@1;F{*Ek@mVwh3VBu7Y_u;)S`1iQ+@gfis+Xat=~fz^FJaSJRtC#|HYK4-'
    'n<n$g5K1ZaeZAaRLR+=`etiw}MZe;2q(q+J+#<Vo+&$-$9F#ntg<B`i&l7|Rl7<<|!5}C5QWZ8mcIm4qWdc&oM0ZPezzug5Mg3jQfs-0?_6~iO`i}U$3<>@L'
    '9XF<<rW=r56Muv6QCox;@+$A`8_%oS(>h-H`5fA%)S1SmZNJ<ieI|nRd2$l_5}-q{M%3>a@`mfckUFu}5SMp$N_I}#o8OZq?2?_<=?!T8uX8!2Rkr*omT)B5'
    'R#Vd3S7yU~4(D-teC0LYq$G*@td|;l43KiX&nT~{zAyyEv-H5FcS8ab#%2f*)`s}*A<jsLl7uw0a^@G<w(&BY0qk!p|K`wdLkb=~=RJqbGtDEZhqP4&wAKTs'
    'hB*+SeIY`rWA8a?pK*Rz64>)~0{*m1J8qmO`<UqwNoKzP&63~Bq+*l)H&AbdACOLmmQmqTe?pp8tY2#Xj_VmTI{Ljm6!1tb-@OkXyS<BPDS#}9wGjqWY<t#g'
    'Q_5U?y+<yetDT-fqcW6GdQF$X)Wl33Wx$^(E<xA{!eUOB(udqyt>&<8wbVmu-(ObXcRzV;q4uMIs?6ipJjS=jR{7TSVQxJB3!GI(IdN;O#mu%9Yn?r^EfY3?'
    ')<hB_l{MTuJ!j$>Sn42iz93ZuQr!@?bJ;VBZn+Id!CPgU&3$)pY(%T5H>7h{x5)@Q7!9HbOQyG3Ddh(3McGamm#i{!T}pc8A&rbJ1zDeiR3<{5uv~k0d*S^Y'
    'DcA(5qfZb0XSl9&4rb567-)nftqD*}mzTzx!7~PExukC+;^Lx8E5VuFCg19KVQjW9BZ3yeTr^EvhQ6{%=ZrQG+R|cSGbf{-xc1><+vs*!c>jAmTHQ!`Poi^t'
    'PhKHb(ljN;J0U&DJJDRiT7b`zoWu*jv&1vK_8Or$e{I`w;<ZwFpEYgCMMoBq%CRyA$SBLZ{<Me-fo4O*o4ut>8!kE^CpO>~IBv&)cn$n&x!1w2zX1I;uJ_{G'
    'xTo4f7tIRB|H4*uEAn>^OlSZthydZ~j+O^1VXT<`bCzKGsa98QYGN)6kPDz|iJxW5DNwE6we1v|PUXhwHn{I!Tc&R7Nw?P-6#KnF^ca=vO<fxDx7MXnyX8`A'
    'JW6C6;4br-@56R)@GzDXJ~N<&D5p}B5E~RA)-dD6tod-p{!ToX@TIyP30L-~OxugBRAws)L9*lo`6r?ZuZa6%d*)^qL#_bj!Z5b2S04Jka_LU^R38BKQ!R(('
    '9}blXK}7X)mK?bPqI!k=&GWFae?f}ndxYAFcji3Hc=;m69AYPnM%R>9#1=Vz4}?jJe-o{=nPNqVIzlm1v|l_QkQW%vJ|O0L8MeLYSlv38qjL;sQ+jnQq0jOW'
    'QVDtH&3bRKZsJ#H=Q^#ZjMB5m#kiKSCMzFeKd*(o+z=cAZ4u@j`0RK7cDsdk81Sbd?xI*$#6%|*3vTImQvSQ7r5oUk2HFyW2V*=hKD(2Dd#QDE)ADVueRm)8'
    'd486%<0Pk6At^bcwp~1LR*pO%<T%_s1H%T&jZQyzY;=MZ&k>aa0p2>^i1*1;*%Yalao&hV%KaPUIKUopYXUy!Os!KKgEzT;iNXuoFe8A&k<`w*n8*^hLAU9X'
    'CVLa_zE@J^iuMif5r`Na#{hg)=WpVy>=Jws>B1w-C4p@F^ZJ<3b1a2jyInl=Z%@w8XXodfx21P|k9%48<_uFRDW8D%6}Q)jH30rMK-JOzq;UcN6JRPckf9Mi'
    'lcYYI5`Ed8sEEWq&4V<V@BRSCg!fN~g1l;vtCDZ6LYcJ^AVsr!UY#odzr%Rm+~oAteR*8&>O~r-%Hc&kD@kP@cOXm0?cfR0^uT8}T;8q%HJ|g@3${I~G)n#C'
    'p#kN>x+;Bm*79Q8M7km<^{kUT`gevedO6R!zu}xE<e2v|A13Sw`1@A=6{*){{*~l4nPS|^D<K{KC@<yw<mbe?1Ru&ge^{PU<(42{7EybN%0=amE2AAw*gY)O'
    's$Am)TCw8#?!spjCB56mp~mu_AV+Ky07eCQ$FtE+X~Sz7vWjyX2ee<}+&lPmAU*cfi#qT-pyd`mqt#La`K_61-2};vhco*Bzkjc?_g)=my#PcGBu#5=l=&Ox'
    '0mgh#b1*y^<CFw5Y8Ac(Xlx?v?ni5Y?T_y)Zz=rmy6~svScA+@_Y<f-A^(9TZ-`wA55jH&^r$4x-|im$rV~<Z(R!_6OON>dY&$Dw=GnRJV+(fT4RRy^We{io'
    '4Nyc)@&`>D)1!5YpHp=L-KNiVg8d|4SwTJ>%GKh%bv&qYH`G#O=LU^D1g}jPx9KKH<tQ*G{!O{WbY1~s4o6x>)&isO9efA2KbUJ(Qgfq7*s!{5Q!-%BGCwL~'
    'etM5q9&O{Nk$+w47I=~jR{63^6bGF9VCry?O!DtP`hN2})CN3H00#=h+|RGhzY<)--zHvO>Kam9#5M`Br!pwTznMd=0>x*voN)Ub=k1|ZsN_ge?Ktx9(3-#L'
    '7R7sigTg?~t&s~R8f2zV!F-OQ{X+Z6(yO($f*>&z$V0twO|Ja9dQUVgs#-SJ#a3qRGK*?~+Fu`XvmrpyNp{lSmk-N{7(1*rM<iKg=LvO=9sGKPX;q*-QAuTQ'
    'r}kM1KDx9U#^)loo4LI^&);cv8pwCA8qaOWbW^QqiLr*Tr(#SX*G4`dNqRFTpr_8j*rAEQXV>tXW$GQChW7KL9{~BmAaruxKNy02neK(-tsOzgklskOAN&0T'
    '%B4x@7|$!@itN2!%kBL1%0R2dbLB`6yK{p%I~X@!8%Ay~^VytpGEBp%>1^^)_0V~?hSrUWNr!KqKVtnHmy=;*_1xp!i#Ycz3j43#DJ#E)^aZ?5Ro-(i-=Ee^'
    '6smPI!Wxg19c#?yy@RT&kIMm$Pix3>)gwj8>W7xPl)@Kl-}_kSWQp#R_Ooo6D}mivU$FCN101_>ogJclxVitqmspRX_IxTaenQz*8JAl2=%3n13CTjpq#^%g'
    '%GX=tbrSy2uz{_PqkQ=H(-3{4Z6-}l*~*j37DGkd2lMA^YyGnnw%jTUeqBV;BV(Y}P_>e%4fMc+WEGo~HUD&Mk)Dp$l6?Ox+ZJcQ8?`Nq(brHuBv2P1^(e&q'
    '7L~fwQusxT2J<6rq+y>@`x9)Hc~97%*7GHMJD$4oY`W5?04O3u&aJZwAcRn(N9tfnF9YrV_Sj*Z|2cN*X<4w}KURlDyCI$xS~-F8k#JsIdscIqzNWC5?YBYR'
    '>wjHSn}%8&*Sy7?b8Dd~`6O)Yu(<I<zwsQ9J`B{<AYTS~Kl%K7_GkAgtlMc3L+%!G47~4LN->$&pFd+wJUGlMp~Oq&UVF<WOFZiKH|4X-dIrM7^WzA9PG+1h'
    'gulsLnNcUh+*GPIQ(p^zv<A}E=32tfegZ8OQXI!gsN%*7j02p<>iHmdp=s}9b!LcbYyr2&GN}Q!T!0)1w%F!F5m&0;Nf>R2(Er{+`h+aCMW0i9hH3oytoj6r'
    'y6I*tY4;5*A3)t_;%@|>RrXJfF~~iHDtBhmPBi7%8f|W`Lu{vl_~>Wdr22=Nm@@{#j_CWyS`h95^K)c#ZJO;swt?6fp6bK?87a7obu9KI<YCwrfo*yx`so$d'
    'P#^~!ZLqEeHIi~;Fj!R{2f3uuPdc%xmuxfzG58Zm*AwG8q34Rv2eVG&YZsyGK^u7T_Sy>+R6Kn>RaXUhVQf+1&&U{7wjddD(J1x2@Jc{$a{g9^o|VUbrxwe)'
    'j`3=Umw>U!pcPfmd4PN+wsrITxcClUADR+UuYLJWd*3`h4b#7CNecWVH1~@!eu!5=i1*V-!s5T8e=Nzp;>KFx#kdXbMSg<*oUEPed;pv);%qG<<esUdS+=9A'
    'C?mDaBHu&A*8I1|?8^N?G}Gm}tg&26Gp&5a$5^Zh!a2Ur-l*2WJ~aA5`tQ-IHMGyzNq&a3Al7qk+Dbke{T$17ZJl-)Jw`g#A-_c8!6A?J)FUi7&?bnp2LIix'
    'nxFfTI#*s%+8gl*$5sz>gg~h&e71&c!}+e=+IeCcpdl#|-VtyfYTd(`VXpxZ>p+bV&I_!U8Li#UJJ0!ZXTuj`KjnaVXwHf6%$l}MnW5YI%zU)f2EsC~;#!C`'
    'l~T5JAL-k|*%CO|ILOy(+BJCAN&fj#9^AQRH<=Ok!trhO+*$PCX1-WTGTRh6P}kT_3R7IWtx=o)+Y1m&3KRy72;&VLGlzQPAZ&9^*C17{`1kKMs9(GnQW=lh'
    'SLelPe8&@W+(uE3^Ms9oSYJTfQ^q)WKyfhaS-_O5@9La5o+BbIIZyMIje)1IVgTX)Y_WyK!Q-hMc+Xmg;Mbs3&eMP}2<ohk>+t~BvX<<om}szg5qs?sq>)I<'
    'nEFbpC@rz0oZy&cudIE(VEZThEmPV`=g+{;1UzGFJq8*?F(=(O;isSK$^OvlC(G7wJbOKN4s-D92%Er|E|k-W!AF(-FZ_F~GvoUrE^T#PPJ0ysMeS)O{YyJO'
    'G5jukUqvaZ^Vu{9Nbisj53g8Li4OGd<%IDz=Vxl^H1<3B9W0l$bH~T2*+Wim2euAnpsvKl_w&%+-%xX6*?7v$@0bSZU5p{_4Kt>AE&zr;MCu9{SAIsUakD+a'
    'bqaN2@!wSJ-29{V4C$^ur;D%XDZjO*?E7xGEmP*)v3BO>H@vi)b8Bpqkj@m4j#Bwx_1<s7j+_z0!@nDzn3kg^@y(FO`i`ubyN2Dpi1Wj>>&ge#_xX&&y0(*p'
    'Gv{5~CTDCCGd|$CiLpk7bzmz_UnAYAlq3JBmzQjx<hTO`O!54p@E8XtcSE^I&eP0g4KjlQ6l-zL{!!;L)dVG#AJ0ifJsIq~9E(f%S>LtXe6&1k&~GQ)EcWMp'
    '$ET3bBCnO1k71jHE$h&?CVVL2Bc**ae&#0TYMuENeh}1%=b39?%{jeK2A=VG1JWM*tJli4w#>rf>wL&!md75-0Ho9E1`QI<Phl*4o)Y@68G9s7o%cMJMlON!'
    'D-|b(kJEWpl~brj;9utcP%hx`JW8-8jTiuNu24N)`Ls&PnrtxFaAdC2z$SA|+3K~h|4z6yd#@^NwEVs$iwF4#Z*AIn4xO`?y<@U&tNQI^uZViz3-6$E!xqLr'
    'og2g65}+4y$Hl=51FgBD>L&Vjtgi#T&Q`|~(mu@+sDm4LGNfpr{4K;V_Im)Oo|V($J_RS&?Bu;x{$4tuBNFy4Jw}ZcN)wc`-bJpLF5nSBRuJ;8kW>Fvzq@b#'
    'sq@u+E^+_EY-H!MFP*dKYox~8iU)TJ#{;xrw$%7L#p@jzFMD%V^WK&I)DWmrK;i^&d|ffd3$D4;KkfBn<tY}=Ae&Z2>9lk&qQrIGrRLwn%-c8<nsbt%Dtp%&'
    '{|)By_VSok_QqOi%<6YPz{i9hNWi~u+l&`C*6t_ZG38p9HJHo!j@v`?AC0kmz7o;9j=E>ag#$KT{LKAA2A&XV2V%V$m=oQb0y!QL`vN{lkHLRJF50MLVJ=#X'
    'JJ~qG;Y@ouj0J&)Z49+LkmlWVfG)ur@5hUCk<Nu&7Xt-=_1qCPZj^gohd<<*W%u<!e>TL2ZL5Z9uceY@*PgJy)7g`f<|QT{$g{uMpM|=FHLh;$#fPc*?mANx'
    'MicfiQoVkj_qK9G*so3FrTjnZ?>3fE`r|;sX6Bw0B{bFWxMoIUY1;rRcH?*w>z-F%&#iSD&olx2fc5;*-hU0K5tiN91@d#3<|hbE|3t+mUJ9j>tk*2m8LxV3'
    'WKLGsLS$Vx<Gtjy44kJmzI*I}A$#Y3R_w)6FthW|j90VvqxW;8p6;>XIsA4W$J}GyGZgE!Utx{-K1M!i3^rj{oKGzOx_I{Wc(%&KtD&DuuA!3672#aV;D8Y4'
    '3gQ(+EXVt=vvuXp8TUdofT`Y3!`FA^|0vEB>-L1cz@)jheLUR<CFffD=*i5kTPdn`@vKw125dh)3FZ0Zt^HHqia3vlR$n6Z2cXYRy#Zp}3Sh^xuI=kn9TliQ'
    'c$Bx}e$V=W=hyLvh3yJ<T&sD19zJXVYnw6Wa1B>>{_-<pjq-iw?!yW8Cc@v4`qhjH<(yhJX;x@JPuu)u19hUMyY`H{Jklb5=sv_w8iyzQ*LaWUIRmL&jf<Nq'
    'E(y*tRK>NyKkg|xcsOf+VjhJs?WgGm`q%*5AaP%CrbHl-OSrrH<eoToc%53lqQ-?}#UciF^&7U(!rNlpVqe$mH`@Nq-C@!0Wyeek0|59>f6SxeGV`+(W89{x'
    'MWO#i?2T{mKr{f~BGz#*t#!FyU+m{~2z_NIqBq3#K)ElV%m(fq$c5Vey5Xz&>87a0FMf9_wa3Gmn2VL;1K?R4OxEuo5ED?226N-$Ou!gD;d%_o-K-PreQPbY'
    'm*1achurhX^h;fz$m%mT<5qe7Yh&IF><Uu+)AIn<*p7Y4pS9P~h3oLTlDGxs|EYL1V_g~d|2lS6IS6=XElV6z-+{b0`Jx<K8P_*tv#D3%%x6P9$%p$(a!GQ$'
    '(NCA_%Zl}(cCC7+v4#pg$KSyK_rOkxGj%5RJo&9=9A@@dUH0SMT88C!M<M&0mvUV09D3uTowDDNpR*vl-`cx|uJq|r_{6WiCu<Hk9v*vStPg)39LLKi<#GS8'
    '6mtzdq_94aW)S8L>TF`~DS0WAOTviph^q#9K#t32NehhBhaKG?m404-FSk4kHFp1e!T<hg^{u>Zv-(-maWZ_?vNf4;^UYU2>;<fZ-ecCYrS})&#Vs5MU;<1^'
    'NO(SHUd1(sfsNlb?vXx!X&uP%vpFs3>=yn(SL{jQKZ#h_<6pi(z8d;yDSrHd$0GkA)`^P)vGE>0Tm;uVu9z>X$*FVP!Nr(4B;S3sBtkUz-4poYK;rz;+(9m('
    'UH;v*6NLWWhKB?wFURW&<N%D^13y<j3})5F=WAKok|IzVgPhwIxOvd8R<`f3$j(11zjgN=9~VDY30GkM!N+!L1&*KV=Fc&1ME)`d6R-Rlc}5xa4Jnyx%=HDk'
    'uc&MfV4I+?8h9=gCQ{+O9L#~duP^V(w$)r~yEJ~l<15Kt*hMPO^6hxrIJ+}q`orQ2{8{tga)!yg9G=lvtUr}&Gv`cU{FLNPx);x$PvhSgY_P_}%6V@o?6<-V'
    '${d7YLsuWr@2=zupnhTJ5auc`Dc)lk0Cr#1HW}Ni#)tQ{I5#0i;dwM-L)eRi&DD7j#QaUx!lqUSDek}Q{?P;UjlW?|p&yuBn0xdQ&s&qXbTQ}q!ramR@b#ZE'
    'Z>IQ++1E<kU%%u&fO86-1OJOT1%00n#1#@p>J4I*H)8Bgn)?v<E8(;zBP(}8d|#NC$cHD0X`5>A`}i_p&mKRYg&*3~yqI>*xoY^&^1VB*EH>IJmZT8&4|p?%'
    'InS9%Qo+x|?wsRv*Z$3YQVl$RQYH<!E3V7y^cd?R#uH<O%Kk6MR%s3(xh{GAjlB4c-{5{U#^-vj$J^97_q+UR-#mYaGb_WtG$g;RJc8yv;~L(o`_x|zux8g;'
    '9iKY6cjyJ)TX^SOr86iuLca_;=A+Lh{=qyKW#1m<w{F$_ADtc}wDTihUkJ{DI2WQM)h}f1L-ccoiLi&@(+9%OT<SVC#FZ9~zk)p|Sbr1i?a#g!jAP}lmkX=i'
    'xZ#tw$GnL+_k(fAn+FA-XaD+53U{5;_4Mth`t)x9==%;<QK?&5G1m&NZ-aJR^=+!(`RAEAs>-+9e(<2UrtBE}u;}HZqU1QLBgTsIQ(ws(fINltC0|dOJaY?{'
    '2Vl;(M!TPTXpz*_twEX2ytI<?{u}GIekwgp)&rLpN7lBb>?!cEoPm2l%qP@%nFE0An24Xwn19%;@GODrh%@`c<|O~|l>|-ttYN8`7WLF_6!ky;evf|2f0jMv'
    '^-5BK*i%dA;Fo-u?B}Vy&asZKhGFvcy7Eh2E??-=Umthl{4!@ehxTLX?6c*sQfGwb?^AIx=IO&2_;Q8aSI_S8ncbL0=j&3ujOIN}DGtbikC)HHk++o??k~@!'
    'f50V?vQ;c0I3l@BkivrcR6cc1W=XK8<#23?x^m;Le;<|ltLWa>_hU-e*x1KF#1@qe^SerZ>sZP?*q_ao+--g#O_L?#W5(?y38Q+0w@z;*N&U!iLrL3*<b`q}'
    'yR1CvMd_BS<(ZbtN%i<S3qt1W?MNAxz1{b1#A*HDVjeQ<c`X@i(|>`UEXIu1lZIGx-SH?FJxBLZufM9aM>);ac~Qd2@t5VjvU2FEH>!L^?KHzTyv*;A?wdDo'
    '5|Gb@J+~S6b8@dm1MXEbK9}aCk(daTFP7Y;aE5YE-l|*a-zURv)EN8lZ;p>*jK%Nwd|%N^Z1kh;W4BKi_8ir^v)9nI9M9hS88Xu%#<-$~_cmkSQ5>WD{xW%2'
    'L#b<_pAGW;#YEVoGv*@A@4flt=51aJvWXPSWyv6Atar_!Q*PY%bH$3=e~J6gkOP%#shNr$y#18oJJJ0h2up)XY2Jw%cRg5tJ;t$hzN}4{@rr$YmUVLC8FfDp'
    'd+I0MMnCrh{klssPr8?Mnl1eW{&E%N)RWg5<gy_*3+F8HJs5Q`j*Fezt|xDj*#}W>FCwZ#e2(+U8(vf8?of)JB#WVMZ-_ZZ;Tt}j*{3v~4{N2Ls-HB=PM%7='
    '@u-xbpSqqdo_l~liOw(O65t3{BHjXy$~D+g!sVmB49Gg|K(9u8hH{9)Zx-thb!H&O8RwzK_2XJwNo%nmC~X(ux)m{<Jg-7^P8h!5^c-Z>#Q(0B7YF7CPbOH)'
    'n9a?3Vl3scd3|ot$Il#MU(QL~&(hHWxn;<Kb<a;j?7MVxgLJI|^hqDZ*#LZ`#TDi!@7jRx+zDI06S0WwvD;?%&1Amg&DS<7tKJg0wOZ8Sn7Xmfm)zo3-s55}'
    '+&JG<uA`;>l|K<r?(3CCfH9l#cZYAcKO15XtHrf@O&n_Ebg6gO<!X+z_j{*TIw04~h`Bkb&as#Q7aVY}4imh$!Iimv{(pP>j^;$NZri_d<D0%WAVEmV7jXv?'
    '5?+*WiJ1uyM%XSWA&9vD{cU$#I*_{i4m!FdL7HRs*=FsvbR9pqfwkr_W`=mg_Fxa-GdGF%>oO<miFA*M%SzY%XXETW+5prg!FuU&BK*A=y9&2}shjHko^Y=+'
    '*1f>b$sy;xiiet#Yi-Uf-V4S!(>oCMlJNk4$Bri2n9L%`6sy7W>R0Pj5!|GiJK-E~coOgov3U=shP%L)T)Wnee50@5;gQ~3u_f3QU7xeC?Wj|*pVgj(52=?|'
    'I4i8p3XWW!{popkH9i3RlXtRrdcNk9r_2^}Yj{8A--Ty@_z_}SB`4~Sur>kq4C{=I_QPM5r^>9h*v{zprTFKEvCe+m{>8=IPpNUx6tUsBuH$iTiN3#Wo1B=`'
    ';YA<}spmUfC(NN>$tW&si8fiSS<wn044_?8Z~hFE;Jp9e!>ZQ)$vj+i>;wzV^)Hi4w)&=+@1n8`_0)Y-iV;63jAQ!{AO9>p4}@V8Jr0t<{B?R<juduFdjGM{'
    'l)k(6k?%b7M%X39VYWVp>%pH$?mF!pE)%C}g3ZMKG;c2F2=j1SaV+<@K7Ph>EJ+*-`~8SKh15@GooDqmVjgfld~Z^I^x*s*^<h3nq65)CVf?*1Q!PsDHuASq'
    'ep$cI*C<pMrTVSIbCzmXDhK448JF%C6z`TRTk6l~AwM&62EpHZE9w{4>!nC|>)i0kLmmsT2eki`yrv&I!<+5kv3QM)FXIH5lXiTXt8q&j*Kg%mMgPvMf6}}2'
    'oV953uFETYwV&onQ_n=?oyky~jj`jCu3@XX_Ug6m#D*y~?nsXC+BnaN`SjULaieIlq)Gf<6Kl2~>@z<#mXG17i^Wi+`nq<VH6}Je4ZyT#=x4E4b#;C##^#D$'
    'E@i7$>-0FtI#17=gt#GN@h!=<q~0m>)fZhLs|WSgsbAVUIp)ll-&H5m=-C853&fb^=B#E(EQk86{#9R|`kq~lO^Xu+wZd?3vs^N)L9B~dJ_5#`m4k~N&C_S&'
    'd+h(=`m&3Q^Ts93rAs_dcD_UU1r+yG-uNa`J6_p5SjUjAij_mL>uBG@oEnUa;~O0n7w6Q)%^)_ju^Xahgv@8Uy^GezV`GTaoOvW(b}^9W%<QH4^SC!rKeBCO'
    'jP@ok{;`XztMB9I@v_hPb00F~_j&unOOTOT%@6Sb!kr+#-L0#mv+|?%Naf1x9%fVHz}nfc9GuvPxq-MhP?{AdOMhlI>LVv5={Ru?qD>r{wU}HP#P$AJ#pv+N'
    'p9ZU`u`X06!F7f>8MxnXxE}b6UEO<&XSw!6MB6X!-I^v2w=q>S@F7s6gK;FAmpk-hi8o@6IXbLU$v#2--;=txCG{SIY5w2$ZGMhJ`p4rK@pU+lejx2H`;3xa'
    'X8Jwno)zw^p?Z-`&b%GXTif=Lzpw76^9VYhZs*{!=c!6|*x2lPp;~Bm8}&|RSVEciZV(iME=#_LeJ3kV@!DK9irv)^pDC={i*f7+?1{r5AP&5<!Y#kFb(}^q'
    'uChV2kNW}ETuiM3_v=b&IfglONH6NHQq=Dub?qRFc_qtdjML#^y#>T;5O(EDaZ@sm#=Il>#`PZEi#ZBdKM!MOjP*a2ZindTiKZ>u8EZZx^7Xg(_wP58%ycxq'
    '8Qt8y=e*n7%-em|_r3d@w~6m%GZXmnJ~zId=Ca;&oV(BDCKLbmE<4UnayJ<xUw_>GUcUeSk$!!F@E_RYRCuKE7!|xV^YgQo4%M=<6yN@#cr}~HJYoM0bNIQ*'
    'nKi=qvo7J@TOP&2W$N`iBexjO;SF}yDedto_@N%I5jYd3R^KVayx316nA)S!qaWlNSxc*aH?salvDPd}yS}=zzURcO_V3^O_IC&uX8k-rj0*V`Yc1r9@O}q8'
    'KLgfYn8RKx1`kSy9c?ahK2!`pOp)xcdiT|6Jpms)z<M0keW(_WI39C9osapx#{3U;<4r%M88`GZ2ietr*bIGE^t1J69ov3%@UTY12+x?|4&FCSBc8R^dAx4J'
    '82A`JnKUpE55)0yMfaLgo^ZX|O!V0uuR{~74^FWP*5KlwS@|o%-H(0=`8Ax=Yn{cvxlZR@^LD`5v1aXB)`n#N_ub)Zs-9skS72wRMmHUGI-jM#pLu-*V`g|3'
    'RbGsGAgiR;2IJ24Uz<Pp-`APHj;~+a8yWwc)mOC+Al}Ju(DxV-H<zDz6MF;qKyEVh=Ph0NN|Ym;=S{B_#I*ObdTb)Na^z`1eKpk3BCV{#QTK!~?*i|&Ukl1P'
    '{ge1{qmLUM3+2nW-22oY9nuuH@Sy{&v6h+Rk1<sHZ}8X27=<;t$;ZMzgfI9%oekx;e)9n9^bhj*4ny}9ELe!+I7!cmbODd7s~JpVG}oh=PIe9l*P)H+T10ir'
    '^>aEL>513|&DHt?-2%hUR4VRA;^*r4k;l&)7c}OE=O6h^+p!#aN&VK3G2<<~g0v1odeIQ)$Q){{7VIJOJea%okD;Dx@+I-zFKAMTE!d-dIE->F;v|-PGwxSo'
    'Wr8VxX1Ed_AIxo_t^wA*R}-zfhdhLh-)KwiYfZn=kK45OFV$4R=iCHG>RrY@72=<uK5ICuEq*$83|LcOvzkioL}U8GF@n3+d0%0DDrP@&O(t&LADwf@V1i28'
    'cKO;?fsImsr{2jY&*rjLHp7j}lg>NL4e>oM+tN+&M{L-woZ}F)dh_?9uW<`tC)b#hAdSJNCDriWxIa3JH4TV~aPLzd{pCY^0@gCT4fmJ5Aa_>i`jw|6T=)6('
    '@?`O$!M|TcLlFBSM({m2$IdSyeuK+rQuTAvLp;Uf0sLy>odBQjlk_^UXCE;n=tt+^Z~P3OFXxA2#ndhhH<9>Jh<~trAi>qz@uS8dlRjyE?S0}q!5Om~oV4cP'
    '_i)-d@&&E@;c=$5PM<Qz8vC_-x{ljxO!~GK&TdLM<x(*1pO$}&UMX@kD-Pzy=q1R6u0i-u#xub6>R3-B|C4;TB|Nj}H8u$=7Kayi@}SrwPuzyE2EYf_+HcqL'
    ';FU;U@eiDz2RyHZ|DDcpxBjNFy^@c5<h(Ta9%RSM_%pmaYTm-{CPg3helrvww$<n8U9a)k(sGA*&J*7%%<=DGk7}7bU+ui`hKKtMG3V(Yh{q1@r~d8a$-{a3'
    'sLyTnx#z8Q<Z(VJ*Hpgvo;17GhR-0<`{A`7yDOD@kp7Px^?i)E?pLJeoWQ*Gkq-pcx?7WlHD6kxKSKDSnDbHlL&EzU9}~1O_iB9Oe!Nx-`(bumBw8(X-FM^Z'
    '`_&S!Ve;k<2VC3sGtG7C&RNa4Fnc^M*Xv{YEUbLp_~fe?>OkuhY+8LybC2h@JabG<Uf~u=N1J-}VscxIrL^i}xaRE~)2y;82CX0v++;Wxrq4;qr^I1(UBB1X'
    'mD106PO68e)XEDMUsj25kr}N$VDc3wI18r3auRk;YCiqP-^eviwHG)a!DsQ*+PR25=J!8fFYctIIBbyr5&03xjvyz(5zeO7SZbKsh7qny*;V36)HPM<N#gjO'
    '>_iL>2jaMCQa?a{PX1G+Fn{f4w6=YU*8OqT-OyS;SbwhyKHwj(vt2cQR$m$S-Z_|G8zVwk0#z59ey8qv$$n<twN>xQ*qA+toY1@vu^+KlWUa6@`h`;0xM(LI'
    '{w4d);~TRl6_%KAzi>{%**mwes1FCs@yGcx;OosyZ__eGZ%x~4i}O}tRk_z1V+ovh(g7f!@ZmPO?(F)r(H==yx9$8HYz*wP`2qIR*78{i-%oRcIX%?F(^|-s'
    '|6WcYe(C2hkI4HT)>KpK1=<IYb+$LSOX51S{OGSa*c-gw$@=~o^D4HUH`e9Uz93uBZyX_BBA-ok&Ts1{U*(d(AMwDqhI!BW>q#LGbCmg<WRVNk&y7#_=)-2}'
    '=Z6!l)!n4PxempV)(V>FuWSfk3jT$CzgmH{JFpjVoWmS|jvpvifH_+-Co=hYd$Cnmn*m$Y)Z7%X{t>Hidh21{EI5aBPWZflcf)7tg8gX0SdV(7BWA8$sNPi;'
    '55M^yU|S%*8WNTp&K>>xF5@7})!kqdV2#A>QHS+EYUe4RhxeNxcIqKc<DH*L-c|Pi;`5njn=Rf?q@!bx5A`}u1C9e32k3CfEMD3-r^+T{;;`NXwT^XujPS2n'
    '>=V&iscrW&`6|SCpN4lXPrBl24s{(5dmCV^VO{LTW#$aS-vzMmbkC{Q_3h4R49hytr=Ab4x7rX-F8K+3FU3t#S>c>cHV)4+W8GQHg4c(24f+oLy{mp_zK8b?'
    'GH=mwpz9IN7S22D`3~{*$>*H!<G58#tVriUGk6LbG2>=k6Q2+Lt;fg7GjSEtShg?@VQwDMqYqz$Y)hZ+(IFh|Gsl<%$t8txbXM;=#yk|`i+vJaB;t7A(;h6T'
    'z0J1wwezs6ukBuU<3p<PM!Sq>vCj9YJPq}x>Tx5zD&|F$_b8(-B=X?D;JduQ-#<rsjw_4MedPNW@5A%MJu9>>gMMQ*UNVkvY~j)f>$h&~U~liO^%LF?O{}|s'
    '|9-^$cV)2*{qBhSZZ~|&ESjQ|9+`9NK55UR?3;CMdt8v~kKR|f;S?6Et2Ce7^-0<SA8-A~Ip&`$tEx9zeqmjHPit#Qebt$6897`aHtAWs+OXy(W$NKL+xtps'
    'k#@5|202iNIZGeh_>RgRiw`+lbddX{w8EIAs<k`%jV8pHu-2CHPLn?vcXZZ4j>mWg_GER&o;R=^vd&lQPBvd4pA++F;+GD&Cv<T||IAkQTXndceBwEO;d<x;'
    '^Sz6cE%;oI6T~_2IqR&w%UC0LADwHXy_>-pRK05(dHldus(j_7r|(}h{G|CV?zIl{xN|Q+v3cx9`r6eRF!QL+Rm?9^eW_{jHHm&>!uo&2bw*ri@N7-uzlC$m'
    'UGMlgdd~3LSJx-b56T~4yrp#nR8Ihni{V0w`EI|une?c~ik>6n_hVm3=4IVD43T$#-QD(bbbatav5tm(d-RMdcI9|xBjdfu;B(#7`zGgxfB)5aINCkVGCp%2'
    '53>1vydJJ2SgVMO(VQ`ab&}dlM0?~p=+-yjN9WMi<u^4;Q{Jxmfp5sUv&GV?J(-$$G;d1%Q>gRD_m8Pnl0Te#k-Qai)RT_zHGJ|btV6dxC%G5rIfNfqb2wPN'
    'tEv}J`5i0lXRQ>?btva$c)%pTLR`XFJMQq$ZtQC?+L&LlYSbtBT(Cc)ZaLRwtBRYO)<5~QD~yL=U9m1<g3ig~&8YV^)wSJk<VB>u0pCB``e<t12zS0{XO)g2'
    'ZB_OA^n0WBL_MoNEBPtmYKpJriEh%@zMtcIGt4pep6oIA`aN!5zK-G!QF^9hc=i#_FXo`R?kCu$$MqZdY?F{4750u!u&z}y8nPiAeB_z0s*y*yZX)A!wdM`g'
    't$BZK!_RB?Kh#;bc0U2zBiGXX0pA$gj>K>pXU{no)XV1jlBHW0)f~~7Wn)vVygOo6LcJ8rKZGwX`m{IT{>OKS5y95y5L+;8S0bESZl51L>rdCwBl2dE9msxB'
    'A8%K1MxpByJ`2X-Ytlm-+Z0~|YheelIlf0)6WShOJncUB1pO`1vjV?li`bw#=~p415!M*yjmSrvR6D2n#XgnFW&&eWJiL(}Nad5S&;K5-?-}m}_u#X||ARJ2'
    'ox86Ao$aWJ<2>Vg$unyl{0-Ke@O}^BT&WptO5uB<9vHNz(@}OG!vSO4uDr_c@}7z27eak3?)tBX)|34iQzIg*ScbJQ^GD`@Ap9!Uakv*?jr9`Dt!5T*t#!RK'
    'TUcCyS+EbEqqWEN8{{TG<>B8$I71L8)A$^hm)nHTo4O|U+AsZIeXJ8?4%5!E*l2Y;<@a=3-TZ53<#(Gwqu%%Cjn0!tc{*$CFZ*$!d$gW|PSdMayG82p_tn_~'
    'b|c5OP;iKa__IczY1zh!I8|W3R!`$i^_llcQ+%K<@c{*=q0%KOpik@{tH<q9)`H5NmBF?hENtB}V7{&c{XfI`-74CVkDWUvnfzm?+nv?B?!7BT#QLJ$`N}sf'
    '{R7e=D}b+0`U{lXKd>&U{k!}#?4u7@V@Kh|KOiSn{QX<SZl=+02anx$*Z)<+dKsKC#M1}wi+<d&9PAl`Ks|EIiN3)a`UB!+);LDv(|9&*ZZ|NFmKK5dMxx`a'
    'ZFz`4NY6uI4`YlB^ny!79C2Xw&QLInb;`XQevxlcz*^tnHh2zxbu#&B@LcS)J^xp;>lG^nZvg(ltoDr94etr@$;9_r@Ln)a1b)Px?eqiIzp}qp?mFu|doS?W'
    '_Mkt;`O)WIr5JQuAD9RAO5h_PHsQYVj5>MVFL=&<Va4^}zw_(`@RX}Z%_SZ6Wj2`0^t0Gk6XGB1Qg32e{*E|X#*2f^q<pK&10v?<tmn61QRiO>PfoGhxl($j'
    'x(Me^xi*aJ&cTwGWz7|+#|hTLgm6Ew?g%qSemAbKVqoPXD}Qs}FdkyO&fHc@lDjEqYPg@p4@AsQWG$M03MYn>Tw0U*I`A6N{kELFpAz<a2L8UjzkYp|k28qt'
    '_ZY*}yZ+*B4K}~<_(C}X80&!YMPKwY<Ihwd+}AHWSIlQ{{b3Cm^N_9X=;YPNC#5y-QD4LO^r-uF;Q9M>KDE79)hk-f9sDYMCbaItxPm0VB2Zclb}oHv)~wON'
    'H5^z}?+wM2a8BQB!N&vtYeF^#o?$P35%q?4)_xQ2xyIfsKI^?-lrP=TeirUt{%j!m@ouh_SGfZx=3mhGu>GXG5Zj^a`G%!u>%-IMCg+VQ7F{CG?rXo{4K|uL'
    '<xlr{)ogKJ4s!XPlK#%|zDG54^kXmwLD%aHj#zJoG5&Zv^WUSo+IGLt7W17??Ppkl#C=mS`WWp0Blb@F{FQ~^>Ggiedt>4#WW0o6s5q`8T~mx{W5&zHv0`2m'
    '=4_(#ts1ukF{8mlQUCt}<_Df-wI^)8r8_z-;Q0Z*Yrpx2;`si<wq|L{LyQ1^X=59T(c`d5SH-->_fE!j!df+T?TB7l(tHr7*%a)-ORpWSV?J0oCSK1PSKFV='
    'nXlv7-A4`%tt;nrRrRK5Uz6$~2FG_+xsu-bae{jeYgI;z*wnFjpGInsaGu|MPPo?a2YkhdF1eZy!hR>kD~UK(9jgoWF%sWueFt?PI-6l^bT5R_h5Q_zzLihF'
    'e!Pu}>G9thCsoKF-m<M7>v2{oHzx4~)dcZOgSh3lia8wI7d=NFYqx)>uXv&A<PzsNoE6}Ymn8?tb?^_2!)Z^hhx<Wqa%Mb|V?Upes}*)S_~HIE$gF;~p8Y<a'
    'Gs&L|`hD-Q-N`hHU9T~1dDYh-z_S9+OpSw*{0Ztri+zwajr9)HHty%peyJK5V|`rAlHrbu@Di*Zc}$9jn0Q(BFq-wq8q2EY&HD$=4YKd`HrVt2?BH-J6)O3c'
    'r-xV``9W^7eC=z_1<H|P%oMiE$RXBQ1uq@jw)a$gnqtqr)^o=Ty6|j&)z<z|xSk^z%`nbEa90ZFaFkw3GiHQoI}%oKc+$O+`%!j`*oROL)6LyD<Gp*+%=Gqc'
    '{Ps4Q+)c;X?Cp4Ro4fJu@9(^u?9{vSGhWu8-reQ1cei($@!e#4pU>aF-`(GH57YZ*yZKS~QYVtFLevkQ&b{EoyOz65f1RWf%Na(l0D}jMI}I)PIEN@s%1}@y'
    'j5<6eYonM_SshHeVT%5AxFIXN>U;N=8gvnv-7^Y7QLs9b;9eWd27sqzx{FG4I3=iK$od_3`<S17yIbirY?^DrcRiT-{r9_^8e2G7ZVGEwxu)zw>1#JYB`mo%'
    'MKEv>UfsD=tiE<UtmIF2v~sgPtUyFIb5|mG7aE0J4<XgmbA*x97TRiwRn1XuHFZ1SkIu;xx{l36YIQ8Z^F7bK0jjlUSJNR_?qM&~U6qp#4Re3HYk%*a?njLb'
    'rOw!yRxJ1!^ubew(+MlfjO35$II>T3l{F&^yn@}R5dd&%J;HUGTgr%&rJ^ogVlpC+Nml}5yrxa-bCmre%WjZkjV)E83VB3QB082chr+1O*TRnd2UO37IVFn$'
    'UhRQB<jv$P)$z?6qQxwaNm+278$pDGD;%Z^SF8-4$Ua!47ya`v>(56|TmeC(B{9fm14PSEm53@ssft6SFtS3_bND-?E>9>yjKE>u&RguNK2PAZfb})O(@hlR'
    'd|^r6U&T(G1+T7$jKJ_-u&3bJNBI1Gil*o~cv2C%xK5RH>T-bJf92sBiJ^=TBf^fGdQWS7`#JJH)cD49BEfoxNahVygPuIO|HEJK3|RGACU-C$Q<;BZU9Hr2'
    'F#;B~oD)SpoZ&yio;_ms-QO8sQB)1jW9)n4DT?YtSIlXg7ca<9LQf+y((ULh<q=U%UFjGtN7YsLRwpHAY~mDH#g0KF+q7*cdho^W>Sa$|LM38zV|5i`JwG~Z'
    ';)R)|%*rewzE+XzL1fXd#Hh0p^UWvQ#G4th@IMFb?)j>Syp7$^%-ZMg@eCc7?o~8{(FR2SVBh);taNbI!aZ~Uz&q+J9D3FCj=keSvPsCKy?^gFu=|F+Tb2DO'
    'zkEtH?)XmC9b}z*IMjQG9aatCoj0hEiq(J2o`T0lYqO#{(^LQ3^ZO7g3~V1UcOe6W$uLz)aJJ-jtI<MN$>5AbRx&tCb4|%^fv3Mgf8l;M*r39BV=E|1s%kol'
    'pQ2tnZ0FaQ-NO#@L3XwsFRG(K+q-=!Wb*y?e*1df9(vj5!~4g>;a8CPU2AU<rGnYB-QXl$W$5LWA(8?+4rk2&sXR4ruB!s`cPb^%!*1^Zs@lQWzVVs2r)<f('
    'foI<At-LzEp{=BdSt_vh`@s1@%w{lm8X3}_Z$?=}%8-57F_R>{H~)TvsI%1(J2p&r-0CbDU5q-nf*PT&Cfds}$k4>^itR@`i}P4(n+`tpH&&gTi(-|!m#Ci8'
    'now$+)?PU+uZ%XI`3%FZQL1jy*m0H#)-^_nf?9XGBunZH!Kl1=vb!#auF}?gv{`QB-klMP66NrF3+vkom3s&Y)(4-LDxFwykvXU=ZvG$>f<iKK2QMiZ!g~1{'
    'bB604(PyxyzjlabtYf!j7{Sl>D~Al^*NxSXSV%-APqC-q`{b$Wn+3Zjj3;g-)fo>u%XF1|@pz}s1k4;cu>qm-L>ZNkU({8zfwMGU*<7bkAN2CS(VrbjS81h0'
    'wrF8zdSd*7u=)abE5+|0-DNn?h#S2REcCPP{MQ-uD6_49l5UN-`|w;x_o>Whq&p*Wzc3?9;+Tcl*v(pRsySaEvjW~9<^~y&^0WtXs1j8@4f@M(cF{S^hm?sS'
    'HV5#>`E#q$OVJyFKaGBq?f?Q`>5K{qR2hB3E;Q>LQ@^K9czu0c?WIcUA}>``&%P%qoJ705-0nGp9<Cy~0sX42^E_}3>s*ifuIh}&?&FNfG7*|#fhsKGFVY%P'
    'pQEg;HC}|I*9e{bZtS6A1R*bw**P;h&DAsRV%Hn`I@IAD`(0H*Rk36k`Ih8^5K>E_rnxfyNL~M<QV|teG5*b9^@C8#F6x5rIj|9uC0J9K0V)t<J@__*10$M4'
    'Dm08R;&b<?*9iO5%-W79h83-27`Ik-m1&oQMX0O)C!LytSog%|(X4L~b#A?`EY%NY{mOHtsf;lwodxQi((P-p{cTVIZ9X06f;GgmH?@K=Y^+h~*}6_j$2F_{'
    '2%|+t&s&#mr2PK8C)5xrS8!)NGi+hUQok8eJ=SB3Yh9S+XU>|V{XyN+7^|x8JnD4H)*%juIGW>Dc$PO*%vtz2*My?R?qNhJ;+cl%k#XqST4_E$V;$Euqvwn='
    'afMhmKh;g++>7>Gt`cPy(aQCCVe2GhE+SbGS%^NbU%}3WMdk(>0To(GzGKfnXIdl5KPv7)=AYISv$~vFH#s3hszE%*XA<W+?2ao@nc&QR<g*LiF&TTN$$Kky'
    'Okw>heoRPhgxKg|-tb)FYNN1ggp}YBq0x|z=?ATzK9cIx8=n6KuGH$YURS2*><+UWV3mW^%yC~=)_lHO{)PNqGayjMEmpx46GRM(N*GVgvnu8EO8KDTa_iaS'
    '6!g;1*p`JNL57n_|3MfakYOG39frvbd{0D-LmaxCSuyDyp?-`?{GNS+XJpsPsW`XYZN{iuJg!i=T(O&~F^^eq3GDs}-)AK13X!KnvKJ5AnqCul*4Rol(Rolz'
    '8+<)t-@$7vLUH3F!nlmcJk9RNv1aUig|2gX4inF}qCo{biv4i>OY$El=<B0$SdJJ17BLErh@ep_4D1&(BZ`~LJyn$dDe*FWhV!%i%7VvMnDoRq5BslZuT*}f'
    'LJZclKR7Q#>qaUX<(Kexzk@Pt<MY)~F{{`7<oh*0Rjx<!H8kho1L$}uQWH=)8d(SSd@D8>Mi|EBU({K>Ok5jW(F(^J%Eq|4qneC?gK%V)SoEpqL$1~$@_L8o'
    '-z0{8eoX#F@gyt}h{{+)e6V?QnCC6#Toace^8q0uvAb$m3XZUbF?aL<Hk{+);sMc)v@Txzlr!2Vc2Uxq#4#;=)?P1rir*+7=dn_A8S`6uP53%rcRDJ+8<mZG'
    'KGTmJmuIzl5yTCR-{^BDu5(5oMAmr{vxDlC8EF+QJw|FwsVa-=PC7?WWTq5SN09Sr*e5@Gsm>_<*$5I5_I2+IZ5&>QQ5Rgnbnm5d6srvTch+3XyQuF**M%T}'
    'kU1bSx1vhO*J_QB!N|#lr@T|rI*I!K9{N->K5Ok9pD2&f@4luAMTYj=em`OGh~_CzOU=GL=m!u-MDu%gZuJs#tkfLn_ncFsyuc^s1mJg)zj}z7cdCxYt(nw3'
    'wy?RWd7}>Vn6<FyScJNo5phc$O`*?*|Bl~@@vZPVjc?XF#eJqaxq@MNVHl!T(@_sQXb?$5^9=Xi{`C$v>b<ko?0AgshTj<$A(~62$i@f`WFjZOl=3&j{&H3!'
    'Bw{qjP}z7<w%#Ng5ITpF6?W8G!F|B~9kVb+uNix9YmZ#MhlOzFwe53`F<dM096R9rtxX7YWk-E?AB&wO#S^92&^jX$K|LePzy5u!+Jl|*PDavgRM%R)`(hLq'
    'IPXI2>~F9aP?2HCX!=9FFZ0<}Kg0Rl3z&go1E1Txe8SqF56dr~+?6eHc`<Xvev0vp6OeLY#6=vs!hD8uD^#ClW+I9mj!EkPebJ;fK!{KIOjK2yySJKNwb;}w'
    '>MFNnK5wlf)#YO1q>KBPVr(~F**MIJ>YB)A{u^z)Lh0x{kcrPsPOEJgA0fs|&Ic9Z@<XBblbQ*#<)IBwKVMQtP3~^OF7B!~E>p~)d}-5dQ&M;VX2G-K3oHJl'
    'ar=NZz`4CrYc=_Bau+-DYgLXQRRz<oCpr1C)VzxIuFPar;xoLLrCh$)S^hWK*BCUO<O7i5#pF6^J*zxpYmzo+-~%qjZ?M;a<PuSlHC+J%*X?N2*eSGhWbWDT'
    '!!iFujB1!0me?(|zTayd-iV>T$|}X>1K8D;e@TtQeI6Az(s~fp5meD@-d<kE#U(1p!!uI{6+92BXQ1sm3!{J<EA&+^D+@>+!0rSwd44CKx_s+bR6Y-Uy_+r9'
    '5a-~(@&0*{7?)xfVxgp*g<eC5(7evJ(mkB-CGFW%Tb^>A_A~f*l<U#CmHDvb?J!S)b>we6r_^VIsn}PY2Paf;X>~$%=KckBoJ%NDtY42eh>pe1AUo&BDta&v'
    'FfQOPKJD4p*2;LQ>kW~pkE<|}Jz<S~Ov-_%gL?2zh<<Hw-C{W}HoI}mWf3(qbq!quxI%4)S>tU~CD_x^Vm>mMpvs|n(p}(;bf$V6s|DA4RjeN?6y<Jw@~Fm1'
    'u_^in`ngo>_p|d}F(wW%uEcII7YSPJK6eF$3G?<5`*-U6Bo-IyaO>V)x#q4ue<*)nMyAm!*VMJzViD`5$o5J31vnd!HQO6+m<17@Ih%N{6vDaVD$Grl*#R9J'
    'r<t!f&x@Ebu<0BF-D7M)Yl7;aS~asZrF~2+hkBlBKbo6p3}pGZ#5i{AsL}N^7w;ojqbcXn=gf|Uj6ZU=)Gn$-eQRxN--gDBn0@uSUJn-@VYu+j`djN*axxnI'
    '{WsV2<?{<|1Jz8_`w2Vc;2?Na?r;(NT!(m*ePLwzH24kj*C=1}4I6xgLQXn<h4EFG-`4ilFhj1f!>NvF{>=o=O*%{OI9_A5Zo=%O?klo?=m(hhyW*LZk7QVS'
    'c|WpRdzCSy^1C<6^^#g=sFo1=JH}1L!=#27rQ+dE`6|Eo`G|#NM4c`4J-^>x{%iY3ItOE)7vE9)?-$2QUqdrv#^(R{cU$zGk(Gmy^%37u{~h;W&&%u(*VW6x'
    'dLwKv+Lzex;k_1&{%?H}J1YLZ?@hj=a;E(Mx_+lPB~BVQtLslOlGDEXBR=05_ERETot!m<N0vIz*<$su{1AUJCZ@od@+HsMr4O+Vb~<6qgBUv%6REjhB(Q8U'
    't0u4Oq-P5;N~_xAk-894)C*G7Jl*(|&L}rF{l1O0*QuIo@>i6PN_$BDtvf;(zo*z(v>(3w!Tqh;6f^OvnBG;jD^#VMMzg0%rw-*BuVQ_)Ssql}nV6%p@FhQI'
    'D{y=<C#OQ};6>Pr*;Ph$Nru^VTt;|4cQBrk&oAg~Rk%vYIq7qy{gZRo?RXURPuw4|>Yv|N2aR-Fac+{-X~VUnmjBcI;wbkhoo1FYALbQ1jDf93>N8G0(D@sw'
    '<eGYolZVtbKKhKQ-?%wQ{d;04O{(2Q>o0t^3&sK!y|=b4P&i7vu3=+Co!=YWXL`O|@8fHH+8ErzlV|V^tNkJd>qlb{$(wgPQd^4e-bx%3R`lx!8O|TuHN7ve'
    'h1KPt%@{qflgJz^m_D_qq)#BLu{6{XCi^kr{W;IaSSwU$K+KEo3bS!!JE?)&Y#!918|{Hfy4U|s{UAIZl%p0rdZdmnHbUk8;Ea+p4dZY+_gP`F{9?|3W}}%>'
    'r*&Z-&z2HnGn0KWWKvhLAjD(Z&v=Mc>47=@vO22Ml~W7VcB?z7cD+`yyZnyYJgp7oU(mTM|JKZGk{iB_i=Ax0%Z@wk{I+y=a}Kr~lLIu2tIUwx)O}ZKPwLt|'
    '-g~l`^XFiFks1%$3|!AoO6`XkbnSi-WpAZ7Y>Ubc{T!ZYj8(Rjw@|f?e0^5(z_DgqX{q__h`m#*bE#U*++|u9tm|uAv&KuVK4v|Os!T7}$Ro>+%kD$#gO~@#'
    'CqGmR7iJ$_FI?*U5&K&+?xv-h4(|CSV^ztooGtoA#!J^v4)z-HgqG4me$;GxZ`jKKao#7L^?dCR9-I_3Zivlo0d{rM<eZ`Wo^yoM_jSTJvvztUJm-6HfBL1m'
    '%FS9BjTs|pSWpYqJ1ZaVTRsQbEr&7H{p^&VckbKIE?8(h6ElKyhxr;#jM|xDeM8%db>X*O@f>CNi)D1i^EL}-xre<=yiRrB*xsp8XhkYsx0qKNm4Z?DJJfro'
    'W3bAivLw7Z=PJsgYG0x{2lt{;pWO--Kyrlr69xycjdvD%%NA@L_?__C#iKVu+k|VI>h@o|0rvTTw`Mawv;7Xv$mTkLF$rURmAY!Ai#wc?A;0m<tv9ZPNdf%i'
    '8h#d8AQ^K2=P#{I^=^#Ap*T9m*5UUy!JhgW;opRv?iKmxHP2&xBdk#11EsTZe5Lw|b(QDoGP_I!zh%_PrJFm?43buF!%N}#^)2P2EcYjy+;apUjp|)$4nrzk'
    'W{(LQw#Lm`xYI~2N-5G}u88tj1=Tk)UsP5{RF-0FCSEhpED^Lu>JApLYiuH%_Y!;T^}2d?$5GX*YUDqIz0L_%#lZ=}nkxFT-nA_gl}kM1!G{(seW+i8axT2r'
    'L~VvXt5wL@mtHLW*S08G6)Mrst<krs>|Q&Wh1xg5eBs~_`FlJjuN`khd0WY0h+nj*1sVCAg<EJdZq<%8NL{^?@nf$MtA1m}|HRyfIqFa>V%CO?-Eq<JCcj$U'
    '))f4TZnFz9EUVP6HJh4N`Od!vMZet&Pz|Kf#n0KQcJ}<+eKLxL9M3=aeO~&S=C3`zKH^;X{ESiBb;c;t%N2Q-ZV?sBgLVKuQa=+6+VGsDsyj36LyV)r9)ikz'
    'xPWt_yq^){#OYyC%W=PbeN*&M1+6A5l+@$Bb{G<Ob?}Y)IX?SPt*dszo~#4-N?)t@LLSE7&me=gVZ+VO$DYq;A~Jfn_ZxVIWwTJZtCTZfvj-b%JY_9X4QQmF'
    'yBO+sO2vF|PTY;#tHQ)HroY#FyjD>?IJ~awrnhZU59PWq#qEE`-nu<mVl#@&rb76I;z9dW{mzxWuzgnlqMT!i&xa^O>0@r7>pY58%(MIW9JUU|_<-NMHe>jf'
    '4f2(bZFDRPYK>QJ^<0)AHearOA=5|anV6Z3GHO3qB8Na&<q|QYT7d#$ckmxkSsr8N6ESX`m9K#~Zn3j5Tn3l@<i|YbA4e-czJI#Z*ir1P@?(}d@TAv5TdjI;'
    '<(y#r2KrfV<zNA^D6*!VBi}2KqM5n|zthoqX7R~YY}~T1akA&R&-@8;e_G2sjtA|&lW_ecgnvTc0&|!>e+~bs_<vEym}#+J^fw1lNo#@cxkOwho&7cR!xcl7'
    'Qle$Nuwr}VzH*K|xfbiLW!<|B`#(IU;!k}kg|<*%idMO2MJ-xOt?77!EPv8Vr~HUOSZI}f;Q#NH>X=f#1wRk_a*^#$T_^7R5eAzEE2%?lCbgxJQTwb;uvfa5'
    'v)8W0j<xa>p4I%__66gZ&f;S0kLSI@2>x1x@x`93HJIOx<);_1?`^R7g}4Z`hgyYgr^Y!>o!nyWsWzKIqodTxN2TLd&TI57)Zd7+!_JYYcSj`?bJh+Y+g*s='
    'qFAwo=4;6oy<v%-Ppu1hpBS5f%I(&gn40)o#|+V!N9Ud_g_q;Zj+0_IFk(0w9+3a%nK@yWiiwhXg9kaZ&s=vHqAy<tpN2VJxQ<??+%n}jEi7|}YVI5laZVUn'
    'f(tsYVaGF9vAq4DH_+5J2t$LIk=fQ}IHYy+#P&|U8(XUdJGCRl46qrKMPki1%ti3=7Wf;ibBr*zr|T?ujs<77n>Sna9F$)uV;^2+S2wDT#3}`QbsO$aOo~a`'
    'n!VLg77ooN^8XBG&CGH(DcuuKftp)5>p$q6&kntj(;M&PKWs*0)4vny6&#gc4wAQ`+W%N#S9@gh6@Gnh>^|0qY<>#ES{Lpsq?m^k4T4C0FEL&-?=SPh<Dk@h'
    'W7zBRL#uaE&IX&iC{Fc@`bjBAMAz{2{-xeSRBMi?fMQ<G-;adF5BCwo+j(_%v*KjM^q=VQa{Bd_IM;(5)%Iov%gWcanDLa7liNE+&x(7ih3W(!9KX{0DCMGa'
    'Jtyh_o5y^`%{l$3CtP|_VGfmIK|Gtk%i6C#{}YBk)8jK)uRh^Ci1vj-9Q_9C6W;~zA7-{!YI2n4y0&lh`fFa(S9$>dc8<Bv0gc%{HE(Sx$=}AP$ZO?PQ;XGe'
    'xiiX!nj7NQ?j26#$Fk2s;o$0Low!UZ0j4qV+kN;K6l@l8YOGccT=>m&{@mUxtT3zRB)%S;-p^4rc}C%K!`P0Q0Oj{5tCy1YuIa-y`4r+VN&gPUR%-vJJ(Kt-'
    'srj0X`J2?(c2T9ums!Bfo(E!{wHZgPJUyH-l*>f*1>u%s=4xST9$+6Galol{KzNP$XoKqm|9xYxaVs8wV7Bqo%e)THIbQh0Xo|Hl9shj1#Bl)brKYqehRiTF'
    'O*s4{UZEZ&c$bA0r%(>Lu(s@z<Qe)NGaL84`;fYYv8}|DX!04PmK#;^m~GL%5$h}Q6eOsYESBF=_mHzlKd-uf?^Jcq8r`o#{gJyN<`!dWagy7QDn<YB`3Oe='
    'u{H7uj;T*bt-b0*uWwRM-<y5Cx{gDp={z6RuFlMhf_#EQR=0MPxlC@=eJ0|RI7jZQlxL{Fm-!_)*J@OCLh9MISnLlgP{i?gFlNMAS6r7uKZSWE^H}RUH9CW>'
    'T!+u1Y&B1+js>u3YgCYecSa0sVJ3D%^(!>CgHFY3EKE)Aiu}xzvv#zy5h<>)kAHexBO|n3_r<x!_?-QKio{Zwr=vR0=%urpSX?^7k;FK}aSriSv#tLA8G8vh'
    'HshQ*<?ZS0pqk`r|MhE?*<H!iMZBc8-B<jk-0Q~5fe^!d;<!-{w5`-s4)yxzy~pdao1+@|0WoFZ6UWwIV)B(5_pP~068+($J+6Cu&+zYvrTee&SohoE9;{{U'
    '`wGO%8Rp<b#rM`?utud&@uynesQ-Abg;}+i)t64DQOCTr_!o1sA<F*{HR4)7vr8Y-8R998k_E3_Qe1{}K>4DM-(`&-tX=Qu`ml(5cm)5Nd`nju#yayZDYnF1'
    '%TkckX9@b%b_^9&6EaF<eykFisP;MMht$&QsP=|&<VG-+;-gkGj}W)cKQT5RuD-x-k4bqY>U$xd3V2I}%4)i99bd|p-@kZR)!@6;6RE7n`MeH0Hs$<vP3S1K'
    'u*Ouc2|N?k9xVDB%z;T>c5AqjzSKN^>UX4P`LcYaFZ;VO<B9bR{rB6L{^}Jx1Nt&^&dux3O8+X?K~!GF;TUkSCRh3Tb;pkn^RC3RO}D%GPViiO={ys*gm@9i'
    'Ug^D)yuRv5K5~)|Nio8`_S4N=P(NxIhm?d(TG+X$%Eyk^f#<6nq;J%n8Rwza#bth5uG3S^Q1LiOe1mI>l{iGbz|9Luj^BzmDfVkdxjn`20XxE6e?|q}!sJvZ'
    '_F2ciS5p6BBCjdcH~U$(eJYn8QeNj;M&Pf=25)3thQF)vFM`K0?~;V?L7(+Jq~15GK_G^Yf7i}G!oNLI%P2DE(^x67=z2PFd%*d^f3Gj`-vQ?1GhND|1+XsR'
    '%#_>_?t$09pAJj@2l~&ZW<z)d>i2{7qOVb-TtBZx<mr6xKdxQ_mH&k2Ul;Lves}4=20904@jW8ePu0xV#U=KA<5|q_Ts_mX<(j_upoB53Te-90ldIlg*(!q@'
    '!u-9i7rpR1$atVnYAGt|e41A8GuX!Xckf|N%~Lh?y;1}Lznb%=*yBlRsb8qVa)jR-6+-HRQfgbM4&t5M8XI2wQS5~9l$IKpr$s-Na)yex8vLKN%emUx4M>@m'
    ')zH)4ALn}FsQ;Sp%ACygIz#@;wnrSUgilU<-y`_L5aT{&P>p&%EJ(jlX}Eg_U#@Pe1&Ch<^Gda}x~-@rHT^;MYXx(O*nJpBh$%|uOo!*1!&*P79{#j<h0kI>'
    '_`||K9FUi8!aPQa>w83X=&R<p_+EYldx|+3#2g{-70hK>U4x!~iF|NqR|lvt4CnA*Lp?fksf`I&ztlua{-05K8T-Bo^Mkoth&w8KH3x0<1<C<(ieheU$!csK'
    'JZJtl;tJ5#fj!C&d#q$9I8%<nib1z|dX#HE4tv3BSnqn4Lbx{<oF?<RUYvs><2|X?(6!#n`F+e>ARjwpQVEw5(0h}r?udNo7vIty_|shm4Z>nP2W@R1NVWCB'
    'w{4J&p|-P?HFCls=Rh9L|JIc1>tZXxelL;74Ca~Zz83bmZq#F!A>|TFxrb_zyv`%{H3~VFD!BdKg!QRvt)5X7>xmz8-7_WFHpXni+IH-^wFl;?YDDjD*?R0n'
    'y8hWEN3`%*Ip^p1;(8>j`CXV_srl*k%Ri^=C#q@M_l@u>s=5`|iBoKeU6b`xZ862JDF1-`tMjG3#hl?-14}teH=2{(QngN8CyIOASI-a`Pss;ZD(tM>7eAJ}'
    '>+Z*UZ=lt)o0Mlz{6%R#fb>%&o!6htfBe($Gw)I8>@#bV@SkAE!Jl~ztXg~OH5nI_a?Kw;tHpCgYV}%44%Tn+NPok*h~H1?^B}z+@a)R|9bA6T^zVl_vMdGg'
    '%!2D^)X(i%b};>U+#Uyv3u64y`D#>#b-WC2us%7!UJ#edfOC6B?Q2K%bjm|owLan_vwJ^U=PTDf_d}5u%ls33gw^<**?x<w8`a?Td*oLW(fR@ZPp>=9iMiMO'
    'bwb=S#IsrI8Q#Tm#gs{{7**r`5H_H<BCbiYbI6HV#@^f2$NQCY!dy?3``V7OE7U{qYZlj{)OEmbm>{3*r>IcDnF02%gn1MFJF0^W`^Z&D^>?$!+U9y!?Fr)Q'
    'YaY0lYMituu6jS=Wc*v#(0q;wdtduTT+g1pw$*%#Z=zzj0;&XJZ>PSm{G9ac>2^i#moSE`aTHz0wsIt%JHdI_<2<6Hw1;ar+5V?K_rJBK+?oUPKw&PtW?_>g'
    'A9|jU7R1r?T=DzjS$j}CVY&wnuI=OnE&lh!x#au1e9!u<{@HvQoLtH|zE&DA%|-NkKU_!1P2NtsXa18o!JN<koqNT97v>uO{kN_m9ID~9C$@A*nplEUh3n(F'
    'vZp$Lr2y~t5SQrPlkHTW&eOELGM|*mr%SU7>NA#)+R9)I^F#kkT`$4s2(r|#*IO+`6Di@{VNW5>DrsGn1F+FI2inWT&61a%UwpP!eq8GMZ_GcCyyny2Q_qq1'
    '6EL<c{QQ~8o=|PIdjMOr!F&ImF@U{?^-(ypy9YVb7OYz9?+^p_+E)I);>t%XCSi$xQ!LZhe)-Sm^SX7o#h7!H9j-|o6`v^=lV?Nx1lBf;kEp-*!?k$n`&O>Q'
    'tQ&;`|BnA|o;~SY3Me<B{I`Gd+Sl%dZ#*mI$MHEQ<rVU8f(>h4JEq}W)xn(YUDp@daQ?oZqd8k#`+V{H$mGfKOak8>YZgj<IW;Cmyd1<CAFOtre4T{%MGSK4'
    'd!@!_=H5F|AWpEGKIOvx{rjMQD!fe>a}HOJk>E_Oxu#N_y^y0Z%Cr}98S=T6-6x!8bE!9q^}G-hTX6FKq9W~mPS%Vq)^$*d$yz(nO$}p2wc@e6_C#Y+`Ie&4'
    'treu<e2)4<qWEq6cu~eq%F#Kh)d$J{q}fcwHESGb^qFD5Y*5)<uK{nAuuoyX-M|?`YXtV4b&d`ZKb3LpU1xcGiPpMQUCF4AV-9#R$Mu!HtbNEeMznnz%Z2%0'
    'QmNMOvm<QnhOqvaBi?d<ym^*!>7M#SFqh2O%llY;qVXoGLBsdU+qEWF3H~G6ud4bMUfYr0fxTUieeKcn{&D)M9c(Vd7|G8-Ip(<gU+h~d*?H(M!oo%C@%Co~'
    'Qy#*l$#X?{M2f<h)c)cz7ar$&iQiXaQgf_4pT-m7*5Ip8olL&e?oI;XclfI&wPV3KJDfAZPbJS_#bu;vp!z~m=TCZI)cecxuY<LD7t=GM`nA3>hloKqL418q'
    'na8kJvH6a{N`7$OifU`9Z*v;uAYP%mu}_#Tr>0#9)!}ijOUNsu#u0HH{sQ@v$X0^?A<wUVPMouzHD6R8FH{SiXaA|4!TF-Prpp7d*0snCz0<<*;de6CPAjPP'
    'Ob=~@1C-`q)WaBd7S`fO;`QSD2gv_p*Th;=sfh{R1je>pMIEk#aHSx&>8vmab@$%8>(^cDbHH4ow`uQ0{UA>XTyv^V(9vj_5Vz&1+}c{}g6g>atlTq!Z^>Hk'
    '?&lY7571?;o5=O<Oi!EQXDL4SL^DC@eb+aWEzJG4R^XmojlFl)&M}IK5x#^g_^}p)Z1D)^F4#e=12GmZ{JUCVDLMs1&8POy^d^1BE`@5XA8~vLs;@7Olk5<K'
    'Clij4So}2WqcP4PQo9wbW8xS~oc;J2!+rLb*o@ekE^%q)Cd=2peBR}%b$es1ePQ>eBQDSJ{gZ8|=%8b*K3F-uuvp(%H}HH+I|uB~$-0B*N4=u*QrSj~1NZMP'
    'mMrzzm5+G&xs|VMX_pD69M*wg)nWW${vB*ZpSWIEa0ZBv87$Lmj(Q$P+p|YFIOU^C?sIkKb(O5;WsM!y9;V7S9_s!y<&VYq_ge2p-SZLo+%b=Fs`0bpUUW2O'
    'nWu<yJ>)~iaZ)+^F8RLbKQFF$FX|sOYeCj}e-5^T*5q=0`tdUupXVAIYvBqcHdK65Qk&=4t`^td$#_D0&5(Vye%9}3*>d6E_s>dGfo&J+1=xL4K@0~V^~I#0'
    '&4Q~#pNi_|i_2>!#pjY<_>i4dHSKi&kMnLt-{OtyhUc94{M5aa=8pWV^rFSqb8XrD%yr=;45(bg=J`VmRY*rs_HKl$uO)ccr5(p4`s%I@VM4E$@_8JsBmJ&1'
    '+<HgWC42JZeW}-cFFHutrzB3tha^ovM@L4-a0AVkIJWs6-FU;+jFDJN)#JXsUaZ<lOwWdeD?M181+KlO-#uLL!}%V{4_EK={q-$%mrKv8xY3o~r*$^|L2j3{'
    '*o<o_Zft}-jq+UBk4Evwu<+%~zlkM^7*8hq&Ji37pPMT0fAQSR`U}=6C`vxnnh&`~+WV=`DF4swclAD83nA?-_5PI0%}=Ab+%LU$>6|^K?=h1j4W=qbYQ>+_'
    'vr#vGh13z4pM1P&etS%<+<i=3qI6|2_hiOUe<!-eQaJ_cuwM~Fh4}vWb=OzC2r-<9pOaG*CvN6p8RHwfy?5|)E`k?N><@deapXCzv5A<%qJJb#xV3*8DUBd='
    '{$1j?4)<~eXls>DkHy!CI7i~O!aho{;h)3xKroFldNiD0W&Ig>Ux`-`A49wy6T;mgmYjK7xL?h~UQqB=nm+h(m<RJXx3BTeO2ZoS^QT14mUf(0ci6Yo&p3Z='
    'KZZ53zdLSirw7a$Ba3GldtLWn%QncnFv1>Juv6SG8{ab)>JOZcHp{K%9*Nhsr#z_21=JXy%{uhVy>T*j<T!gNq;S`BzFN)z?5BHB>w7yvd%p5R_2>NAvIDfP'
    'YwhKC$I)Pz?b;s9)r9R9oCBVRb!X(Jz<wX_<u-Umkmd{HFR+)BwS2PPZbCSt$}Tmh5o6P&=bFY&-aAx(YD}&A(>LFf>(y^P@|{-op6bW6xj7!NHWNB0X#FGq'
    'WM&wr17)2N#%|8_SzV93Z`V5WD85Jh=>EI&T$g>FFa~Mk=3g<k`;af7=Sb(NW9IMse#sZsn=i-e@w{0SOEPvzYjM<Kc-1}yhfNyoUH)0+i)vB6@d><JWoFqY'
    '%8A#EPqQPPL5M3M=GdXWWGc6HHUOW1-?N`<2*0(QVcPdcy-sx|^JnVPuSfTI^7!9W>mu%R<v*bB{O$AI>H$rQcO>7S;=EAY^%FnTV5n}>#w@O1>?h~FaN&&{'
    'or73iNaTKyTy989J4tVsHC)~A2wrW+`q}%1^dg?Po~U}Xi|}5R#k7;2&z1hSJ@&v4a;p!gX7l`o`~V;Cy~ci+q+=rc2hS$k*X?Fkw8uM`<LfhqHc9j14Ke!9'
    ')O-KJIK6q?9*W`HsbIgQHCNcfS~-OH3h1-Q%aX3u^)l-)et0(S2iSYHhO<iX4<hDusW_D|Ps{z_l>hm(Y_5fEOXV*4&^*AUXU%1$aj{(U{(!iVutTCjC3Q5!'
    '61FhMm!%#ci<>1?<Il$G6&pBD`(YRT$Utz)TvvYWhu50J1+%037Ze|>7vc%Oq`~HJ8{yu6vDRIDjze*PJ$Xj+JM|uO#Ny($H_X$>Ie~~@TBugAE^vt(@QU|P'
    'd63;8D>*N<Uke)hP5oKlxhBp(_U8*%Ziv^3*4?(I`#q^IZaz%%`H0^qpQ%YLbqMj|xcssW_ZkPwpxy2ctEx}m;lH<HZL9-}-UVt_-tw5Y{92-piK@-V=)Rh|'
    'eH>pxIuE_+cSZbU4%P^bH^*9r@A>+3i#eeyn}#=7d!k=fUE+6WuWmBwaq5rksGA!>&O_!O)P2JIED!15(|LX4w+NrkEH>6m_m`A?sU+rtzw7Uq^Di;3RvMoQ'
    'Ljcd;qL@)>VQGGZpJ~}9$G`Ag!(Jng{h+Xxu}_a|GLPjK*R-XVKT+$9bfIRm)_MhYmHn_<47VBSh~{2CEj;;7@C_f-V|6vx+&_5s*?ryiT5ZSpV#LYE`=M4y'
    'n`h0vuK2v|JU;m?on=sL6&urL(CW6j`EI-G{~{k|*(wGr|EV4LrDk{aqU=#K^Hpqi^Tk#nxdxPG?{rT%?(;QigZM8nHfiUku6sVI91UM1j5#3fY5jZ^g(o;&'
    'z?mQyxF;GPVsqlgTx)1CpPAw&QT()y&SO%Y+;B|9a#P3ZUpRbi(>`H(f@vRoG2%rm3P-KhAG6mEb5JC6xK$aa)zkWqNi|)A+0|!I3$@ulCfTNMd_#-hrn$Yi'
    '79{>q9HxIJPZ{QU0o#v#V(>m6Z@s0?6UOuSKOEzHdV}v<8lFs8@6F}cYcq4e^|4nxvQvdSc&gNtc06J43J=?k*AVWVV~F$WIBREvyzm<BD@DZXn!h#sEyMsI'
    'PtD4Frs`3QZ(Sp_4m$SvE8l~?iaeQYLx0V--}Wh-cP1Pp<=-`)LimJGd(Gu*)ccOOxADxLGH*1lKi+SB&!;+n-@Di6R00<heGWOY3bX(I@BjSY|N75<{+7+('
    '{^$Sw*MCOuzBkI>PxIsK&Fv(Uy}Oxa?<V<7ZaU6RG8xZ*%e~!<Z~fa`X7c9c^OL*DeLk1V-jDpZoA*iPKmYST|MUL=aG9;Z'
)

LEGACY_SOURCE_COMMIT = '632bd65c20af2018c7f7e0c92293969e8e29cc65'
LEGACY_SOURCE_SHA256 = {'engineering_common.py': '2ff25a987fe0a03426f545bbe927b356562ffe4c3d32e16c193a3b07360333cd', 'engineering_drills.py': '72e237b071154e0e307abbd8b0483565cfd1c0766ad37ae533539cc9f960ed87', 'maintenance.py': 'a0928f78f77dc7d160be42b6b8cf019de82e0363c26978f95f025d8c36562e89', 'task_archive.py': '267e62e354a8f53fcafee2981225ee7b2d3a8ff94e44373ae60ed1fc79efc1fd', 'task_context.py': '2047da78f735767573fbd751073eeb4444142012f440ca6b4b13b439f931854c', 'task_lifecycle.py': 'ec0603c0e0663bfcee7427fc0330b19e71162ca796dc05ed7429396fdd007dc5', 'task_queue.py': '688e4d0fbc4b47e31660a8299184ad9120da8283c6f321fc3803dd9672c2d340', 'task_worker.py': '47c01e1f0f6acaabd7fc226cd6341887142f1790129f77527670c7df8558e788', 'tenon.py': 'be91b58f5c246d0274f27d503fc2d0019a3a4c69630da155d7d853328b9a4ed0'}


class ControllerFixture(RepositoryFixture):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        super().setUp()
        self.root = self.root.resolve()
        self.store = TaskStore(self.root)
        self.processes = []
        self.addCleanup(self.release_children)
        self.release = self.root / "release"
        hooks = self.root / "empty-hooks"
        hooks.mkdir()
        self.run_git("config", "core.hooksPath", str(hooks))

    def release_children(self):
        self.release.write_bytes(b"release")
        for process in self.processes:
            if process.poll() is None:
                process.communicate(timeout=30)

    def version(self, marker="one", *, full=False, legacy=False):
        scripts = self.root / "scripts"
        if scripts.exists():
            shutil.rmtree(scripts)
        if full:
            shutil.copytree(ROOT / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            scripts.mkdir()
            original = leases.target_launcher(leases.common_directory(ROOT), subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).decode().strip()) if legacy else None
            if legacy:
                legacy_tenon = subprocess.check_output(["git", "-C", str(ROOT), "show", "632bd65c20af2018c7f7e0c92293969e8e29cc65:scripts/tenon.py"])
                tree = ast.parse(legacy_tenon)
                install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install")
                literal = next(node.value.value for node in ast.walk(install) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "launcher" for target in node.targets)
                    and isinstance(node.value, ast.Constant))
                (scripts / "task_lifecycle.py").write_text("# legacy fixture\n", encoding="utf-8")
                (scripts / "tenon.py").write_text("def install():\n    launcher = " + repr(literal) + "\n" + BODY, encoding="utf-8")
            else:
                tree = ast.parse((ROOT / "scripts/tenon.py").read_text(encoding="utf-8"))
                install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install")
                literal = next(node.value.value for node in ast.walk(install) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "launcher" for target in node.targets)
                    and isinstance(node.value, ast.Constant))
                for name in leases.ENTRIES:
                    text = "from __future__ import annotations\n" + leases.EARLY_GUARD + "\n# " + marker + "\n"
                    if name == "tenon.py":
                        text += "def install():\n    launcher = " + repr(literal) + "\n"
                    (scripts / name).write_text(text + BODY, encoding="utf-8")
                shutil.copyfile(ROOT / "scripts/controller_leases.py", scripts / "controller_leases.py")
        (scripts / "marker.txt").write_text(marker, encoding="utf-8")
        self.run_git("add", "--all", "scripts")
        self.run_git("commit", "--quiet", "-m", "owned controller " + marker)
        sha = self.run_git("rev-parse", "HEAD").decode().strip()
        self.run_git("update-ref", "refs/remotes/origin/dev", sha)
        return sha

    def install(self, sha):
        self.run_git("update-ref", "refs/remotes/origin/dev", sha)
        return tenon.install(self.root, Backend())

    def deployed(self, sha):
        from task_archive import deploy_controller
        return deploy_controller(self.store, sha).parent.parent

    def start(self, sha, *arguments):
        entry = self.store.root / "controllers" / sha / "scripts/task_lifecycle.py"
        process = subprocess.Popen(leases.entry_command(entry, *arguments), cwd=self.root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        return process

    def old(self, directory):
        os.utime(directory, (time.time() - 172800, time.time() - 172800))

    def legacy_source(self):
        import base64, zlib
        directory = self.root / "legacy-source"
        if not directory.resolve().is_relative_to(self.root.resolve()) or (directory.exists() and leases.redirected(directory)):
            raise ValueError("Legacy fixture storage escaped its isolated test repository")
        scripts = directory / "scripts"
        if scripts.exists():
            return scripts
        shutil.copytree(ROOT / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__", "controller_leases.py"))
        compressed = base64.b85decode(LEGACY_SOURCE_B85)
        decoder = zlib.decompressobj()
        expanded = decoder.decompress(compressed, 2 * 1024 * 1024)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise ValueError("Legacy source fixture exceeds its fixed expansion bound")
        record = json.loads(expanded)
        if set(record["files"]) != set(LEGACY_SOURCE_SHA256):
            raise ValueError("Legacy source fixture inventory differs")
        if record["commit"] != LEGACY_SOURCE_COMMIT:
            raise ValueError("Unexpected legacy fixture version")
        for name, item in record["files"].items():
            payload = base64.b64decode(item["base64"])
            if hashlib.sha256(payload).hexdigest() != item["sha256"] or item["sha256"] != LEGACY_SOURCE_SHA256[name] or Path(name).name != name:
                raise ValueError("Legacy source fixture identity differs")
            (scripts / name).write_bytes(payload)
        return scripts

    def legacy_version(self, marker):
        scripts = self.root / "scripts"
        if scripts.exists():
            shutil.rmtree(scripts)
        shutil.copytree(self.legacy_source(), scripts)
        (scripts / "marker.txt").write_text(marker, encoding="utf-8")
        self.run_git("add", "--all", "scripts")
        self.run_git("commit", "--quiet", "-m", "known legacy target " + marker)
        return self.run_git("rev-parse", "HEAD").decode().strip()

    def legacy_child(self, code, *args):
        header = "import sys,json,os\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
        return subprocess.run([sys.executable, "-B", "-c", header + code, str(self.legacy_source()), str(self.root), *map(str,args)],
            cwd=self.root, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)


class ControllerExecutionTests(ControllerFixture):
    def test_same_version_cached_processes_share_without_serializing_business(self):
        sha = self.version(); self.install(sha)
        before = snapshot(self.store.root / "controller-leases")
        first = self.start(sha, "hold", "--release", str(self.release))
        second = self.start(sha, "hold", "--release", str(self.release))
        a, b = packet(first), packet(second)
        self.assertEqual(a["pid"], first.pid)
        self.assertEqual(b["pid"], second.pid)
        self.assertNotEqual(a["pid"], b["pid"])
        self.assertEqual(snapshot(self.store.root / "controller-leases"), before)
        self.release.write_bytes(b"release")
        first.communicate(timeout=20); second.communicate(timeout=20)
        self.assertEqual(first.returncode, 0); self.assertEqual(second.returncode, 0)

    @unittest.skipUnless(os.name == "nt", "Native Windows access-denied classification")
    def test_native_access_denied_is_unknown_preserved_and_never_busy(self):
        sha = self.version(); directory = self.deployed(sha); self.old(directory)
        anchor = self.store.root / "controller-leases" / (sha + ".lock")
        self.assertTrue(anchor.resolve().is_relative_to(self.root))
        original = anchor.read_bytes()
        identity = next(csv.reader(io.StringIO(subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True))))[1]
        tool = str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe")
        # Deny FILE_READ_DATA only: GENERIC_READ really fails with 5, while
        # READ_CONTROL/WRITE_DAC remain available to restore this owned fixture.
        prepared = subprocess.run([tool, str(anchor), "/deny", "*" + identity + ":(RD)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if prepared.returncode:
            self.skipTest("NOT_RUN: native ACL fixture capability unavailable; " + prepared.stderr.decode(errors="replace"))
        try:
            from ctypes import wintypes as W
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
            kernel.CreateFileW.restype = W.HANDLE
            handle = kernel.CreateFileW(str(anchor), 0x80000000, 3, None, 3, 0x80, None)
            self.assertEqual(handle, ctypes.c_void_p(-1).value)
            self.assertEqual(ctypes.get_last_error(), 5)
            observed = leases.version_observation(self.store.root, directory)
            self.assertEqual(observed["state"], "UNKNOWN")
            self.assertNotEqual(observed.get("error_type"), "ControllerBusy")
            self.assertNotIn(sha, maintenance.prune_controllers(self.root, True)["removed"])
            self.assertTrue(directory.exists())
        finally:
            subprocess.run([tool, str(anchor), "/remove:d", "*" + identity], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(anchor.read_bytes(), original)

    def test_unknown_source_abi_cannot_fall_back_or_replace_working_installation(self):
        first = self.version("known"); self.install(first)
        before = (self.store.root / "bin/installation.json").read_bytes()
        path = self.root / "scripts/controller_leases.py"
        path.write_text(path.read_text(encoding="utf-8").replace("CONTROLLER_LEASE_ABI = 1", "CONTROLLER_LEASE_ABI = 99", 1), encoding="utf-8")
        self.run_git("add", "scripts"); self.run_git("commit", "--quiet", "-m", "unsupported fixture ABI")
        unknown = self.run_git("rev-parse", "HEAD").decode().strip()
        with self.assertRaises(ValueError):
            self.install(unknown)
        self.assertEqual((self.store.root / "bin/installation.json").read_bytes(), before)

    def test_delete_recomputes_new_active_task_reference_and_preserves_archives(self):
        first = self.version("eligible"); directory = self.deployed(first); self.old(directory)
        second = self.version("current"); self.install(second)
        archive = self.store.root / "archives/private-original.zip"
        archive.parent.mkdir(); archive.write_bytes(b"private archive must survive all controller GC")
        before = archive.read_bytes()
        original = maintenance.retained_versions
        calls = []
        def add_reference(store):
            calls.append(1)
            if len(calls) == 3:
                store.create({"task_id": "new-active", "branch": "dev-new-active", "worktree": str(self.root / "build/active"),
                    "status": "ACTIVE", "host_platform": os.name, "controller": str(directory / "scripts/task_lifecycle.py")})
            return original(store)
        with mock.patch.object(maintenance, "retained_versions", side_effect=add_reference):
            result = maintenance.prune_controllers(self.root, True)
        self.assertGreaterEqual(len(calls), 3)
        self.assertNotIn(first, result["removed"])
        self.assertTrue(directory.exists())
        self.assertEqual(archive.read_bytes(), before)

    def test_existing_bytecode_is_preserved_but_never_used_by_readonly_entry(self):
        sha = self.version(full=True); installed = self.install(sha)
        source = self.store.root / "controllers" / sha / "scripts/engineering_health.py"
        marker = self.root / "bad-bytecode-ran"
        compiled = Path(py_compile.compile(str(source), doraise=True))
        header = compiled.read_bytes()[:16]
        malicious = compile("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_bytes(b'untrusted bytecode')\n", str(source), "exec")
        compiled.write_bytes(header + marshal.dumps(malicious))
        before = snapshot(self.store.root)
        process = subprocess.run([sys.executable, installed["entry"], "doctor", "--full"], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertIn(process.returncode, {0, 2}, process.stderr.decode(errors="replace"))
        self.assertEqual(json.loads(process.stdout)["kind"], "engineering-health")
        self.assertFalse(marker.exists())
        self.assertEqual(snapshot(self.store.root), before)

    def test_real_old_installer_blocks_new_cache_until_explicit_new_source_install(self):
        sha = self.version("new-target", full=True)
        old = self.legacy_child("""import tenon
from ci_gate import REQUIRED_JOBS
class Backend:
 name='fixture/tenon'
 def refresh(self): pass
 def latest_run(self,sha,event):
  return {'workflow_run':{'id':1,'run_attempt':1,'head_sha':sha,'event':event,'status':'completed','conclusion':'success'},'jobs':[{'name':name,'run_id':1,'run_attempt':1,'status':'completed','conclusion':'success'} for name in REQUIRED_JOBS|{'ci-required'}]}
print(json.dumps(tenon.install(Path(sys.argv[2]),Backend())))
""")
        self.assertEqual(old.returncode, 0, old.stderr.decode(errors="replace"))
        entry = json.loads(old.stdout)["entry"]
        self.assertFalse((self.store.root / "controller-leases").exists())
        before = snapshot(self.store.root)
        blocked = subprocess.run([sys.executable, entry, "doctor"], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(blocked.returncode, 2)
        self.assertEqual(json.loads(blocked.stdout)["status"], "BLOCKED")
        self.assertIn("source installer", json.loads(blocked.stdout)["reason"])
        self.assertEqual(snapshot(self.store.root), before)
        # Keep a Git-verified installer bundle outside the disposable workspace
        # and outside the controller namespace before any automatic cleanup.
        bundle = self.root / "preserved-installer" / sha
        shutil.copytree(self.store.root / "controllers" / sha, bundle)
        leases.verify_source(bundle, self.root, sha)
        (self.root / "scripts").rename(self.root / "retained-source-scripts")
        new_code = """import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import tenon
from ci_gate import REQUIRED_JOBS
class Backend:
 name='fixture/tenon'
 def refresh(self): pass
 def latest_run(self,sha,event):
  return {'workflow_run':{'id':1,'run_attempt':1,'head_sha':sha,'event':event,'status':'completed','conclusion':'success'},'jobs':[{'name':name,'run_id':1,'run_attempt':1,'status':'completed','conclusion':'success'} for name in REQUIRED_JOBS|{'ci-required'}]}
print(json.dumps(tenon.install(Path(sys.argv[2]),Backend())))
"""
        migrated = subprocess.run([sys.executable, "-B", "-c", new_code, str(bundle / "scripts"), str(self.root)],
            cwd=self.root, env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(migrated.returncode, 0, migrated.stderr.decode(errors="replace"))
        installed = json.loads(migrated.stdout)
        self.assertEqual(installed["controller_lease_abi"], 1)
        self.assertFalse((self.root / "scripts").exists())
        ready = subprocess.run([sys.executable, installed["entry"], "doctor"], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(ready.returncode, 0, ready.stdout.decode(errors="replace"))

    def test_real_legacy_rollback_and_old_gc_fence_keep_live_abi1_until_new_gc_release(self):
        active = self.version("new-executor"); self.install(active)
        directory = self.store.root / "controllers" / active
        process = self.start(active, "hold", "--release", str(self.release))
        self.assertEqual(packet(process)["pid"], process.pid)
        first = self.legacy_version("one"); result = self.install(first)
        self.assertEqual(result["controller_lease_abi"], 0)
        self.assertEqual(result["controller_lease_state"], "UNKNOWN_LEGACY")
        second = self.legacy_version("two"); result = self.install(second)
        self.old(directory)
        self.assertEqual((directory / "controller.json").is_file(), True)
        old = self.legacy_child("""import maintenance
from task_store import TaskStore
print(json.dumps(maintenance.prune_controllers(Path(sys.argv[2]),True)))
""")
        self.assertEqual(old.returncode, 0, old.stderr.decode(errors="replace"))
        observed = json.loads(old.stdout)
        row = next(item for item in observed["versions"] if item.get("commit") == active)
        self.assertEqual(row["state"], "UNCLASSIFIED")
        self.assertNotIn(active, observed["removed"])
        self.assertTrue(directory.exists())
        legacy = subprocess.run([sys.executable, result["entry"], "doctor"], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(legacy.returncode, 0, legacy.stdout.decode(errors="replace"))
        self.assertEqual(json.loads(legacy.stdout)["dev_sha"], second)
        self.assertEqual(leases.version_observation(self.store.root, directory)["state"], "HELD")
        self.release.write_bytes(b"release"); process.communicate(timeout=20)
        self.assertIn(active, maintenance.prune_controllers(self.root, True)["removed"])
        self.assertTrue((self.store.root / "controllers" / second).exists())

    def test_legacy_reverse_lock_order_exits_bounded_then_new_admission_retries(self):
        sha = self.version(); self.install(sha)
        code = """import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from task_store import TaskStore,FileLease
store=TaskStore(Path(sys.argv[2]))
with FileLease(store.root/'locks/entry-install.lock'):
 print(json.dumps({'stage':'old-entry-install'}),flush=True)
 sys.stdin.readline()
 try:
  with FileLease(store.root/'locks/integration.lock',timeout=0.1): pass
 except TimeoutError: print(json.dumps({'stage':'old-retry'}),flush=True)
"""
        with leases.integration_scope(self.store):
            child = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.legacy_source()), str(self.root)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.root)
            try:
                self.assertEqual(packet(child)["stage"], "old-entry-install")
                child.stdin.write(b"continue\n"); child.stdin.flush()
                with leases.mutation_context(self.store):
                    pass
                output, error = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, error.decode(errors="replace"))
                self.assertIn(b"old-retry", output)
            finally:
                if child.poll() is None:
                    child.communicate(input=b"continue\n", timeout=10)

    def test_startup_lease_wins_and_repeated_install_keeps_anchor_identity(self):
        first = self.version("first")
        self.install(first)
        directory = self.store.root / "controllers" / first
        anchor = self.store.root / "controller-leases" / (first + ".lock")
        before = (anchor.read_bytes(), leases._file_identity(anchor))
        process = self.start(first, "hold", "--release", str(self.release))
        self.assertEqual(packet(process)["pid"], process.pid)
        self.install(first)
        self.assertEqual((anchor.read_bytes(), leases._file_identity(anchor)), before)
        second = self.version("second"); self.install(second)
        third = self.version("third"); self.install(third)
        self.old(directory)
        result = maintenance.prune_controllers(self.root, True)
        self.assertNotIn(first, result["removed"])
        self.assertTrue(directory.exists())
        self.release.write_bytes(b"release")
        process.communicate(timeout=20)
        result = maintenance.prune_controllers(self.root, True)
        self.assertIn(first, result["removed"])
        self.assertFalse(directory.exists())

    def test_gc_wins_startup_is_explicitly_blocked_before_business(self):
        first = self.version("first"); directory = self.deployed(first)
        second = self.version("second"); self.install(second)
        self.old(directory)
        self.assertIn(first, maintenance.prune_controllers(self.root, True)["removed"])
        marker = self.root / "must-not-run"
        process = self.start(first, "--marker", str(marker))
        output, error = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 2, error.decode(errors="replace"))
        self.assertEqual(json.loads(output)["status"], "BLOCKED")
        self.assertFalse(marker.exists())

    def test_owned_child_keeps_own_pid_lease_after_its_parent_exits(self):
        first = self.version("first"); self.install(first)
        directory = self.store.root / "controllers" / first
        parent = self.start(first, "spawn", "--release", str(self.release))
        value = packet(parent)
        self.assertEqual(value["pid"], parent.pid)
        self.assertNotEqual(value["child"]["pid"], parent.pid)
        self.assertEqual(value["child"]["pid"], value["spawned_child_pid"])
        # The live child still owns the inherited stderr pipe; wait for the
        # parent PID itself rather than incorrectly waiting for child EOF.
        parent.wait(timeout=20)
        self.assertEqual(parent.returncode, 42)
        second = self.version("second"); self.install(second)
        third = self.version("third"); self.install(third)
        self.old(directory)
        self.assertNotIn(first, maintenance.prune_controllers(self.root, True)["removed"])
        self.release.write_bytes(b"release")
        deadline = time.monotonic() + 5
        while leases.version_observation(self.store.root, directory).get("held") is True:
            if time.monotonic() >= deadline:
                self.fail("Owned child lease did not release")
            time.sleep(0.01)
        self.assertIn(first, maintenance.prune_controllers(self.root, True)["removed"])
        parent.communicate(timeout=20)

    def test_missing_or_replaced_known_anchor_is_never_recreated(self):
        sha = self.version(); self.install(sha)
        anchor = self.store.root / "controller-leases" / (sha + ".lock")
        saved = self.root / "retained-anchor"
        anchor.rename(saved)
        try:
            with self.assertRaises(ValueError):
                self.install(sha)
            self.assertFalse(anchor.exists())
            result = maintenance.prune_controllers(self.root)
            self.assertEqual(next(row for row in result["versions"] if row.get("commit") == sha)["state"], "UNKNOWN")
        finally:
            saved.rename(anchor)
        anchor.rename(saved)
        try:
            anchor.write_bytes(saved.read_bytes())
            self.assertNotEqual(leases._file_identity(anchor), leases._file_identity(saved))
            with self.assertRaises(ValueError):
                self.install(sha)
            self.assertEqual(leases.version_observation(self.store.root, self.store.root / "controllers" / sha)["state"], "UNKNOWN")
        finally:
            anchor.unlink()
            saved.rename(anchor)

    def test_fresh_cache_readonly_entry_routes_create_no_files_or_records(self):
        sha = self.version(full=True); value = self.install(sha)
        self.store.create({"task_id": "probe", "branch": "dev-probe", "worktree": str(self.root), "status": "ACTIVE", "host_platform": os.name})
        before = snapshot(self.store.root)
        for arguments in (("doctor", "--full"), ("task", "context", "--id", "probe"), ("--help",), ("maintenance", "prune-controllers")):
            process = subprocess.run([sys.executable, value["entry"], *arguments], cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertIn(process.returncode, {0, 2}, process.stderr.decode(errors="replace"))
            if arguments[0] == "doctor":
                self.assertEqual(json.loads(process.stdout)["kind"], "engineering-health")
        self.assertEqual(snapshot(self.store.root), before)
        self.assertFalse(any((self.store.root / "controllers" / sha).rglob("__pycache__")))

    def test_installed_repo_abbreviations_and_poisoned_git_environment_cannot_touch_control(self):
        sha = self.version(full=True); value = self.install(sha)
        control = self.root / "control"
        control.mkdir()
        subprocess.run(["git", "init", "--quiet", str(control)], check=True)
        (control / "keep.txt").write_bytes(b"outside original")
        before = snapshot(control)
        for spelling in ("--rep", "--rep="):
            args = [spelling + str(control)] if spelling.endswith("=") else [spelling, str(control)]
            result = subprocess.run([sys.executable, value["entry"], *args, "doctor"], cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")
        environment = os.environ.copy()
        environment.update(GIT_DIR=str(control / ".git"), GIT_WORK_TREE=str(control), GIT_INDEX_FILE=str(control / ".git/foreign-index"))
        result = subprocess.run([sys.executable, value["entry"], "doctor"], cwd=self.root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout)["dev_sha"], sha)
        self.assertEqual(snapshot(control), before)

    def test_bound_commit_keeps_normal_git_identity_and_auth_configuration(self):
        sha = self.version(); value = self.install(sha)
        control = self.root / "control"; control.mkdir()
        subprocess.run(["git", "init", "--quiet", str(control)], check=True)
        before = snapshot(control)
        config = self.root / "normal-user-config"
        config.write_text("[credential]\n helper = fixture-auth-configuration\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(GIT_DIR=str(control / ".git"), GIT_WORK_TREE=str(control),
            GIT_INDEX_FILE=str(control / ".git/foreign-index"), GIT_CONFIG_GLOBAL=str(config),
            GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="core.worktree", GIT_CONFIG_VALUE_0=str(control),
            GIT_CONFIG_KEY_1="user.email", GIT_CONFIG_VALUE_1="must-not-override@example.invalid")
        result = subprocess.run([sys.executable, value["entry"], "commit"], cwd=self.root, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
        data = json.loads(result.stdout)
        self.assertEqual(data["author"], "Tenon test fixture <fixture@example.invalid>")
        self.assertEqual(data["credential_helper"], "fixture-auth-configuration")
        self.assertEqual(snapshot(control), before)
        self.assertIn("bound-commit.txt", self.run_git("show", "--name-only", "HEAD").decode())


class InstallationTransactionTests(ControllerFixture):
    def test_process_exit_pending_is_readonly_then_recovers_from_verified_bundle(self):
        entry = self.legacy_owned(); before = self.entry_tuple()
        target = self.version(full=True); cache = self.deployed(target)
        bundle = self.root/'preserved-installer'/target
        shutil.copytree(cache, bundle); leases.verify_source(bundle, self.root, target)
        code = """import sys,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import tenon
from ci_gate import REQUIRED_JOBS
class Backend:
 name='fixture/tenon'
 def refresh(self):pass
 def latest_run(self,sha,event):
  return {'workflow_run':{'id':1,'run_attempt':1,'head_sha':sha,'event':event,'status':'completed','conclusion':'success'},'jobs':[{'name':name,'run_id':1,'run_attempt':1,'status':'completed','conclusion':'success'} for name in REQUIRED_JOBS|{'ci-required'}]}
original=tenon._publish_entry_file
def crash(path,raw):
 original(path,raw)
 if Path(path).name=='tenon.py':os._exit(73)
tenon._publish_entry_file=crash
tenon.install(Path(sys.argv[2]),Backend(),git_alias=True)
"""
        crashed = subprocess.run([sys.executable, '-B', '-c', code, str(bundle/'scripts'), str(self.root)], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(crashed.returncode, 73, crashed.stderr.decode(errors='replace'))
        pending = self.store.root/'installation-pending.json'
        self.assertTrue(pending.exists())
        after_crash = snapshot(self.store.root)
        blocked = self.doctor(entry)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn('recover-only', json.loads(blocked.stdout)['reason'])
        self.assertEqual(snapshot(self.store.root), after_crash)
        (self.root/'scripts').rename(self.root/'retained-original-scripts')
        recovered = subprocess.run([sys.executable, '-B', str(bundle/'scripts/tenon.py'), '--repo', str(self.root), 'install', '--recover-only'],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(recovered.returncode, 0, recovered.stdout.decode(errors='replace')+recovered.stderr.decode(errors='replace'))
        self.assertEqual(json.loads(recovered.stdout)['status'], 'ROLLED_BACK')
        self.assertEqual(self.entry_tuple(), before)
        self.assertEqual(self.doctor(entry).returncode, 0)
        self.assertFalse(pending.exists())

    def legacy_owned(self):
        sha = self.legacy_version("owned-install")
        self.run_git("update-ref", "refs/remotes/origin/dev", sha)
        result = self.legacy_child("""import tenon
from ci_gate import REQUIRED_JOBS
class Backend:
 name='fixture/tenon'
 def refresh(self):pass
 def latest_run(self,sha,event):
  return {'workflow_run':{'id':1,'run_attempt':1,'head_sha':sha,'event':event,'status':'completed','conclusion':'success'},'jobs':[{'name':name,'run_id':1,'run_attempt':1,'status':'completed','conclusion':'success'} for name in REQUIRED_JOBS|{'ci-required'}]}
print(json.dumps(tenon.install(Path(sys.argv[2]),Backend(),git_alias=True)))
""")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return Path(json.loads(result.stdout)["entry"])

    def entry_tuple(self):
        binary = self.store.root / "bin"
        return {"files": {name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), getattr(path.stat(), "st_file_attributes", None))
                if path.exists() else None for name in tenon._ENTRY_FILES for path in [binary/name]},
                "alias": tenon._alias_values(self.root)}

    def doctor(self, entry):
        return subprocess.run([sys.executable, "-B", str(entry), "doctor"], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def test_each_publication_failure_restores_exact_usable_legacy_tuple(self):
        entry = self.legacy_owned()
        self.assertEqual(self.doctor(entry).returncode, 0)
        before = self.entry_tuple()
        target = self.version("new-transaction-target", full=True)
        original_file, original_alias, original_json = tenon._publish_entry_file, tenon._set_alias_values, tenon.write_json
        points = ['tenon.py', 'alias', 'metadata-before', 'metadata-after'] + (['tenon.cmd'] if os.name == 'nt' else [])
        anchor_identity = None
        for point in points:
            with self.subTest(point=point):
                fired = []
                def publish(path, raw):
                    original_file(path, raw)
                    if Path(path).name == point and not fired:
                        fired.append(True); raise OSError('controlled publication failure after '+point)
                def alias(repo, values):
                    original_alias(repo, values)
                    if point == 'alias' and not fired:
                        fired.append(True); raise OSError('controlled alias publication failure')
                def write(path, value):
                    metadata = Path(path) == self.store.root/'bin/installation.json'
                    if metadata and point == 'metadata-before' and not fired:
                        fired.append(True); raise OSError('controlled final record failure before write')
                    original_json(path, value)
                    if metadata and point == 'metadata-after' and not fired:
                        fired.append(True); raise OSError('controlled final record failure after write')
                with mock.patch.object(tenon, '_publish_entry_file', side_effect=publish), \
                        mock.patch.object(tenon, '_set_alias_values', side_effect=alias), \
                        mock.patch.object(tenon, 'write_json', side_effect=write):
                    with self.assertRaises(OSError):
                        tenon.install(self.root, Backend(), git_alias=True)
                self.assertTrue(fired)
                self.assertEqual(self.entry_tuple(), before)
                self.assertEqual(self.doctor(entry).returncode, 0)
                self.assertFalse((self.store.root/'installation-pending.json').exists())
                anchor = self.store.root/'controller-leases'/(target+'.lock')
                current_identity = leases._file_identity(anchor)
                if anchor_identity is not None:
                    self.assertEqual(current_identity, anchor_identity)
                anchor_identity = current_identity

    def test_fresh_failure_removes_only_its_known_tuple_and_preserves_unknown_files(self):
        self.version(full=True)
        binary = self.store.root/'bin'; binary.mkdir()
        note = binary/'keep-user-note.txt'; note.write_bytes(b'unrelated original')
        original = tenon.write_json
        def fail(path, value):
            if Path(path) == binary/'installation.json':
                raise OSError('fresh installation metadata failure')
            original(path, value)
        with mock.patch.object(tenon, 'write_json', side_effect=fail), self.assertRaises(OSError):
            tenon.install(self.root, Backend(), git_alias=True)
        self.assertEqual(note.read_bytes(), b'unrelated original')
        self.assertFalse(any((binary/name).exists() for name in tenon._ENTRY_FILES))
        self.assertEqual(tenon._alias_values(self.root), [])
        self.assertFalse((self.store.root/'installation-pending.json').exists())
        (binary/'tenon.py').write_bytes(b'unowned entry with no installation record')
        before = snapshot(binary)
        with self.assertRaises(ValueError):
            tenon.install(self.root, Backend())
        self.assertEqual(snapshot(binary), before)

    def test_alias_config_failure_before_or_after_atomic_commit_restores_usable_owned_tuple(self):
        entry = self.legacy_owned(); before = self.entry_tuple()
        target = self.version('atomic-alias-target', full=True)
        original_run = subprocess.run
        anchor_identity = None
        for moment in ('before-commit', 'after-commit'):
            with self.subTest(moment=moment):
                fired, mutations = [], []
                def fault(argv, *args, **kwargs):
                    updating = (isinstance(argv, list) and 'alias.tenon' in argv and
                                any(option in argv for option in ('--replace-all', '--unset-all', '--add')))
                    if updating:
                        mutations.append(list(argv))
                    if updating and '--replace-all' in argv and not fired:
                        fired.append(True)
                        if moment == 'after-commit':
                            result = original_run(argv, *args, **kwargs)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            return subprocess.CompletedProcess(argv, 128, stdout=b'', stderr=b'controlled failure returned after config commit')
                        raise subprocess.CalledProcessError(128, argv, stderr=b'controlled atomic config failure')
                    return original_run(argv, *args, **kwargs)
                with mock.patch.object(tenon.subprocess, 'run', side_effect=fault), \
                        self.assertRaises(subprocess.CalledProcessError):
                    tenon.install(self.root, Backend(), git_alias=True)
                self.assertEqual(fired, [True])
                self.assertTrue(mutations)
                self.assertTrue(all('--replace-all' in argv and '--unset-all' not in argv and '--add' not in argv
                                    for argv in mutations))
                self.assertEqual(len(mutations), 1 if moment == 'before-commit' else 2)
                self.assertEqual(self.entry_tuple(), before)
                self.assertEqual(self.doctor(entry).returncode, 0)
                through_alias = subprocess.run(['git', '-C', str(self.root), 'tenon', 'doctor'], cwd=self.root,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                self.assertEqual(through_alias.returncode, 0, through_alias.stderr.decode(errors='replace'))
                self.assertFalse((self.store.root/'installation-pending.json').exists())
                current_identity = leases._file_identity(self.store.root/'controller-leases'/(target+'.lock'))
                if anchor_identity is not None:
                    self.assertEqual(current_identity, anchor_identity)
                anchor_identity = current_identity

    def test_independent_empty_or_multiple_alias_state_is_not_treated_as_internal_progress(self):
        entry = self.legacy_owned(); before = self.entry_tuple()
        self.version('independent-alias-target', full=True)
        original_write = tenon.write_json
        for independent in ([], ['!echo independent one', '!echo independent two']):
            with self.subTest(independent=independent):
                def fail(path, value):
                    if Path(path) == self.store.root/'bin/installation.json':
                        tenon._set_alias_values(self.root, [])
                        for alias in independent:
                            self.run_git('config', '--local', '--add', 'alias.tenon', alias)
                        raise OSError('metadata failed after independent config update')
                    original_write(path, value)
                with mock.patch.object(tenon, 'write_json', side_effect=fail), self.assertRaises(RuntimeError):
                    tenon.install(self.root, Backend(), git_alias=True)
                self.assertEqual(tenon._alias_values(self.root), independent)
                self.assertEqual(self.entry_tuple()['files'], before['files'])
                self.assertTrue((self.store.root/'installation-pending.json').exists())
                unchanged = snapshot(self.store.root)
                with self.assertRaises(RuntimeError):
                    tenon.recover_installation(self.root)
                self.assertEqual(tenon._alias_values(self.root), independent)
                self.assertEqual(self.entry_tuple()['files'], before['files'])
                # Only the owned recovery journal timestamp may advance.
                self.assertEqual(set(snapshot(self.store.root)), set(unchanged))
                tenon._set_alias_values(self.root, before['alias'])
                self.assertEqual(tenon.recover_installation(self.root)['status'], 'ROLLED_BACK')
                self.assertEqual(self.entry_tuple(), before)
                self.assertEqual(self.doctor(entry).returncode, 0)
        with self.assertRaises(ValueError):
            tenon._set_alias_values(self.root, ['!echo one', '!echo two'])
        self.assertEqual(self.entry_tuple(), before)

    def test_independent_alias_change_is_preserved_and_recovery_retains_its_journal(self):
        entry = self.legacy_owned(); before = self.entry_tuple()
        self.version(full=True)
        original = tenon.write_json
        unknown = '!echo independently changed local alias'
        def fail(path, value):
            if Path(path) == self.store.root/'bin/installation.json':
                tenon._set_alias_values(self.root, [unknown])
                raise OSError('metadata failed after an independent alias change')
            original(path, value)
        with mock.patch.object(tenon, 'write_json', side_effect=fail), self.assertRaises(RuntimeError):
            tenon.install(self.root, Backend(), git_alias=True)
        self.assertEqual(tenon._alias_values(self.root), [unknown])
        self.assertTrue((self.store.root/'installation-pending.json').exists())
        self.assertEqual(self.entry_tuple()['files'], before['files'])
        tenon._set_alias_values(self.root, before['alias'])
        result = tenon.recover_installation(self.root)
        self.assertEqual(result['status'], 'ROLLED_BACK')
        self.assertEqual(self.entry_tuple(), before)
        self.assertEqual(self.doctor(entry).returncode, 0)
