# (Gravado em UTF-8 COM BOM: sem ele o PowerShell 5.1 lê como ANSI e o acento quebra as strings.)
# Smoke test das ligações do televendas (Fase 3) contra a API REAL.
#
#   .\tests\smoke-televendas.ps1 -Usuario RAFAEL -Senha '****' -CodParc <cliente da FILA INTERNA>
#
# Grava ligações de verdade no Postgres (schema televendas) para o cliente informado:
# escolha um da fila interna que possa ser "sujado". No fim imprime o SQL de limpeza.
# Armadilha do PowerShell 5.1: o corpo de uma resposta de ERRO vem em
# $_.ErrorDetails.Message (GetResponseStream volta vazio) — já tratado em Chamar.
param(
    [Parameter(Mandatory = $true)][string]$Usuario,
    [Parameter(Mandatory = $true)][string]$Senha,
    [Parameter(Mandatory = $true)][int]$CodParc,
    [string]$Api = "http://192.168.255.6:5000"
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$falhas = 0
$criadas = New-Object System.Collections.Generic.List[int]

function Chamar($metodo, $rota, $corpo = $null, $token = $script:token) {
    $h = @{}
    if ($token) { $h["Authorization"] = "Bearer $token" }
    $p = @{ Method = $metodo; Uri = "$Api$rota"; Headers = $h; ContentType = "application/json; charset=utf-8"; UseBasicParsing = $true }
    if ($null -ne $corpo) { $p["Body"] = [Text.Encoding]::UTF8.GetBytes(($corpo | ConvertTo-Json -Depth 5)) }
    try {
        $r = Invoke-WebRequest @p
        return @{ status = [int]$r.StatusCode; corpo = ($r.Content | ConvertFrom-Json) }
    } catch {
        $st = [int]$_.Exception.Response.StatusCode
        $txt = $_.ErrorDetails.Message
        return @{ status = $st; corpo = $(if ($txt) { $txt | ConvertFrom-Json } else { $null }) }
    }
}

function Conferir($nome, $cond, $detalhe = "") {
    if ($cond) { Write-Host "OK      $nome" -ForegroundColor Green }
    else { Write-Host "FALHOU  $nome  $detalhe" -ForegroundColor Red; $script:falhas++ }
}

# 1. Login e sessão
$l = Chamar POST "/api/auth/login" @{ usuario = $Usuario; senha = $Senha } $null
Conferir "login" ($l.status -eq 200) $l.status
$script:token = $l.corpo.token
$s = Chamar GET "/api/televendas/sessao"
Conferir "sessão com perfil" ($s.status -eq 200 -and $s.corpo.perfil) "$($s.status) $($s.corpo.erro)"
Conferir "sem token = 401" ((Chamar POST "/api/televendas/chamadas/iniciar" @{ codParc = $CodParc; lista = "INTERNA" } "").status -eq 401)

# 2. Corrida: dois /iniciar ao mesmo tempo, do mesmo operador, têm de resultar em UMA ligação.
$bloco = {
    param($api, $tok, $cod)
    $b = [Text.Encoding]::UTF8.GetBytes((@{ codParc = $cod; lista = "INTERNA" } | ConvertTo-Json))
    try {
        $r = Invoke-WebRequest -Method POST -Uri "$api/api/televendas/chamadas/iniciar" -Headers @{ Authorization = "Bearer $tok" } -ContentType "application/json" -Body $b -UseBasicParsing
        "$([int]$r.StatusCode)|$($r.Content)"
    } catch { "$([int]$_.Exception.Response.StatusCode)|$($_.ErrorDetails.Message)" }
}
$j1 = Start-Job $bloco -ArgumentList $Api, $script:token, $CodParc
$j2 = Start-Job $bloco -ArgumentList $Api, $script:token, $CodParc
$res = @($j1, $j2) | Wait-Job | Receive-Job
Remove-Job $j1, $j2
$ids = @($res | ForEach-Object { ($_ -split "\|", 2)[1] | ConvertFrom-Json } | ForEach-Object { $_.id } | Sort-Object -Unique)
Conferir "corrida: os dois 201" (@($res | Where-Object { $_ -like "201|*" }).Count -eq 2) ($res -join " / ")
Conferir "corrida: UMA ligação só (a 2ª é a mesma, 'retomada')" ($ids.Count -eq 1) ($ids -join ",")
$id = [int]$ids[0]; $criadas.Add($id)
$t = Chamar GET "/api/televendas/travas"
Conferir "travas: cliente aparece uma vez" (@($t.corpo.dados | Where-Object { $_.codParc -eq $CodParc }).Count -eq 1)

# 3. Renovar e validações do registro
Conferir "renovar" ((Chamar PUT "/api/televendas/chamadas/$id/renovar" @{}).status -eq 200)
Conferir "finalizar sem resultado = 400" ((Chamar PUT "/api/televendas/chamadas/$id/finalizar" @{ obs = "x" }).status -eq 400)
Conferir "desfecho sem 'atendeu' = 400" ((Chamar PUT "/api/televendas/chamadas/$id/finalizar" @{ resultado = "NAO_ATENDEU"; desfecho = "VENDA" }).status -eq 400)
Conferir "NUNOTA inexistente = 400" ((Chamar PUT "/api/televendas/chamadas/$id/finalizar" @{ resultado = "ATENDEU"; notas = @(@{ nunota = 1; tipo = "PEDIDO" }) }).status -eq 400)
$f = Chamar PUT "/api/televendas/chamadas/$id/finalizar" @{ resultado = "ATENDEU"; desfecho = "INFORMACAO"; contato = "SMOKE TEST"; obs = "smoke-televendas.ps1 — pode apagar"; retornoEm = (Get-Date).AddDays(1).ToString("yyyy-MM-ddT10:00") }
Conferir "finalizar = 200" ($f.status -eq 200) "$($f.status) $($f.corpo.erro)"
Conferir "finalizar de novo = 409" ((Chamar PUT "/api/televendas/chamadas/$id/finalizar" @{ resultado = "ATENDEU" }).status -eq 409)
$c = Chamar POST "/api/televendas/chamadas/$id/cancelar" @{}
Conferir "cancelar depois de registrar não desfaz (200, continua FINALIZADA)" ($c.status -eq 200 -and $c.corpo.situacao -eq "FINALIZADA")
$h = Chamar GET "/api/televendas/clientes/$CodParc/historico"
Conferir "histórico traz a ligação" (@($h.corpo.dados | Where-Object { $_.id -eq $id }).Count -eq 1)
$a = Chamar GET "/api/televendas/agenda"
Conferir "agenda traz o retorno de amanhã" (@($a.corpo.dados | Where-Object { $_.chamadaId -eq $id }).Count -eq 1) "$($a.status) $($a.corpo.erro)"

# 4. Descartar: idempotente, e descartada não se registra
$n = Chamar POST "/api/televendas/chamadas/iniciar" @{ codParc = $CodParc; lista = "INTERNA" }
Conferir "nova ligação depois da registrada = 201 (não retomada)" ($n.status -eq 201 -and -not $n.corpo.retomada)
$id2 = [int]$n.corpo.id; $criadas.Add($id2)
Conferir "cancelar = 200" ((Chamar POST "/api/televendas/chamadas/$id2/cancelar" @{}).status -eq 200)
Conferir "cancelar de novo = 200 (idempotente)" ((Chamar POST "/api/televendas/chamadas/$id2/cancelar" @{}).status -eq 200)
Conferir "registrar descartada = 409" ((Chamar PUT "/api/televendas/chamadas/$id2/finalizar" @{ resultado = "ATENDEU" }).status -eq 409)
Conferir "cancelar via ?token= (sendBeacon)" ((Chamar POST "/api/televendas/chamadas/$id2/cancelar?token=$($script:token)" @{} "").status -eq 200)

Write-Host ""
if ($falhas) { Write-Host "$falhas FALHA(S)" -ForegroundColor Red } else { Write-Host "TUDO OK" -ForegroundColor Green }
Write-Host "Limpeza (rodar no psql do servidor):"
Write-Host "  DELETE FROM televendas.chamada WHERE id IN ($($criadas -join ', '));"
