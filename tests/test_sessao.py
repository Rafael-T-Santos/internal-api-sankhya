"""Teste do login compartilhado (auth.py) e da sessão do televendas — SEM banco.

O Sankhya (MobileLoginSP.login) e o Oracle são simulados; nada é gravado.
Rodar da raiz do repositório:  python tests/test_sessao.py
Rode antes de todo deploy que mexa em auth.py, televendas.py ou no login da cobrança.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["AUTH_SECRET"] = "x"*40
import cx_Oracle
import app as appmod, auth, televendas, cobranca

class Cur:
    def __init__(s, cenario): s.c=cenario; s.r=None
    def execute(s, sql, binds=None):
        if "TSIUSU" in sql and "UPPER(NOMEUSU)" in sql: s.r=[(25,"RAFAEL")]
        elif "AD_TLVPERFIL" in sql:
            if s.c=="semtabela": raise cx_Oracle.DatabaseError("ORA-00942: table or view does not exist")
            s.r={"gerente":[("GERENTE","S")],"operador":[("OPERADOR","S")],"inativo":[("OPERADOR","N")],"sem":[]}[s.c]
        elif "TGFVEN" in sql: s.r=[] if s.c=="gerente" else [(41,"ANA TLV")]
        else: s.r=[]
    def fetchone(s): return s.r[0] if s.r else None
class Con:
    def __init__(s,c): s.c=c
    def cursor(s): return Cur(s.c)
    def close(s): pass
cen={"v":"operador"}
fake=lambda: Con(cen["v"])
auth.conectar_oracle=fake; televendas.conectar_oracle=fake
auth.servico_sankhya=lambda n,b: {"status":"1"} if b["INTERNO"]["$"]=="certa" else {"status":"0","statusMessage":"Usu%C3%A1rio%2FSenha%20inv%C3%A1lido."}
c=appmod.app.test_client()
ok=True
def chk(nome, cond):
    global ok; ok &= bool(cond); print(("OK  " if cond else "FALHOU ")+nome)
r=c.post("/api/auth/login",json={"usuario":"rafael","senha":"certa"}); tok=r.json["token"]
chk("auth/login 200 com token", r.status_code==200 and r.json["codUsu"]==25)
r2=c.post("/api/cobranca/login",json={"usuario":"rafael","senha":"certa"})
chk("alias cobranca/login mesmo formato", r2.status_code==200 and set(r2.json)==set(r.json))
r=c.post("/api/cobranca/login",json={"usuario":"rafael","senha":"errada"})
chk("senha errada 401 com msg decodificada", r.status_code==401 and r.json["erro"]=="Usuário/Senha inválido.")
chk("login sem senha 400", c.post("/api/auth/login",json={"usuario":"x"}).status_code==400)
H={"Authorization":"Bearer "+tok}
chk("escrita cobranca sem token 401", c.post("/api/cobranca/chamadas/iniciar",json={}).status_code==401)
chk("token adulterado 401", c.get("/api/televendas/sessao",headers={"Authorization":"Bearer "+tok[:-2]+"xx"}).status_code==401)
r=c.get("/api/televendas/sessao",headers=H)
chk("sessao operador 200 + codVend + perfil normalizado", r.status_code==200 and r.json["perfil"]=="OPERADOR" and r.json["codVend"]==41 and r.json["nomeUsu"]=="RAFAEL")
cen["v"]="gerente"; r=c.get("/api/televendas/sessao",headers=H)
chk("gerente sem vendedor -> codVend null", r.status_code==200 and r.json["codVend"] is None and r.json["perfil"]=="GERENTE")
cen["v"]="inativo"; chk("perfil inativo 403", c.get("/api/televendas/sessao",headers=H).status_code==403)
cen["v"]="sem"; chk("sem perfil 403", c.get("/api/televendas/sessao",headers=H).status_code==403)
cen["v"]="semtabela"; r=c.get("/api/televendas/sessao",headers=H)
chk("tabela inexistente 503", r.status_code==503 and "AD_TLVPERFIL" in r.json["erro"])
# token antigo (assinado com COBRANCA_SECRET) continua valendo
del os.environ["AUTH_SECRET"]; os.environ["COBRANCA_SECRET"]="y"*40
t2=auth.emitir_token(25,"RAFAEL"); chk("fallback COBRANCA_SECRET", auth.ler_token(t2)["codUsu"]==25)
print("TUDO OK" if ok else "HOUVE FALHA"); sys.exit(0 if ok else 1)
