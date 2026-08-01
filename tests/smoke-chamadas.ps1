# Smoke test da régua de chamadas — rodar DEPOIS de cada deploy que mexa em
# cobranca.py. É o único teste automatizado do projeto; ver README > Testes.
#
#   .\tests\smoke-chamadas.ps1 -Senha ****
#   .\tests\smoke-chamadas.ps1 -CodParc 12366 -Usuario RAFAEL -Senha ****
#
# Padrão: CODPARC 11107 (FARMACIA NOSSA SENHORA DAS CANDEIAS, 2 boletos vencidos).
# O operador sai do login: as rotas de escrita tiram o CODUSU do token da sessão,
# então o teste precisa de uma senha de verdade do Sankhya.
#
# Roda contra a API REAL: não há banco de teste, então ele GRAVA REGISTROS DE
# VERDADE em AD_COBRCHAMADA/ITEM/ANEXO para um cliente real. Escolha um cliente
# que você aceite sujar — no fim o script imprime o SQL de limpeza já com os
# CODCHAMADA gerados.
#
# O que ele cobre: escrita sem sessão recusada, token adulterado recusado,
# sequences gerando PK, trava de 15 min, conflito 409 com nufinsTravados, corrida
# real (dois /iniciar em paralelo), cancelar idempotente, renovar, anexo (grava e
# lê de volta o CLOB), URL javascript: recusada, ORDEM por título com desfechos
# independentes, finalizar duas vezes, receptiva que não conta na régua, e as
# validações de payload.
#
# NÃO cobre: a expiração dos 15 minutos (não dá para esperar num teste). Para
# conferir à mão, com uma chamada EM_ANDAMENTO aberta:
#   UPDATE AD_COBRCHAMADA SET DHEXPIRA = SYSDATE - 1 WHERE CODCHAMADA = <id>;
#   COMMIT;
# Depois disso GET /api/cobranca/locks tem que voltar vazio.

param(
  [int]$CodParc = 11107,
  [string]$Usuario = "RAFAEL",
  [Parameter(Mandatory = $true)][string]$Senha,
  [string]$Base = "http://192.168.255.6:5000"
)

$ErrorActionPreference = "Stop"
$falhas = 0
$criadas = @()
$Token = $null   # preenchido pelo login; as rotas de escrita exigem sessão

function Passo($n, $texto) { Write-Host "`n[$n] $texto" -ForegroundColor Cyan }
function Ok($texto) { Write-Host "    OK   $texto" -ForegroundColor Green }
function Falha($texto) { $script:falhas++; Write-Host "    ERRO $texto" -ForegroundColor Red }

function Chamar($metodo, $rota, $corpo, [switch]$SemToken) {
  $p = @{ Method = $metodo; Uri = "$Base$rota"; ContentType = "application/json"; UseBasicParsing = $true; TimeoutSec = 60 }
  if ($Token -and -not $SemToken) { $p.Headers = @{ Authorization = "Bearer $Token" } }
  if ($null -ne $corpo) { $p.Body = ($corpo | ConvertTo-Json -Depth 6) }
  try {
    $resp = Invoke-WebRequest @p
    return @{ status = [int]$resp.StatusCode; corpo = ($resp.Content | ConvertFrom-Json) }
  } catch {
    $r = $_.Exception.Response
    if ($null -eq $r) { throw }
    # No PS 5.1 o corpo da resposta de erro vem em ErrorDetails.Message; ler o
    # GetResponseStream() aqui devolve string vazia (o stream já foi consumido).
    $texto = $_.ErrorDetails.Message
    if (-not $texto) {
      try { $texto = (New-Object System.IO.StreamReader($r.GetResponseStream())).ReadToEnd() } catch { $texto = "" }
    }
    $json = $null
    try { $json = $texto | ConvertFrom-Json } catch { $json = [pscustomobject]@{ erro = $texto } }
    return @{ status = [int]$r.StatusCode; corpo = $json }
  }
}

Write-Host "Alvo: $Base  |  cliente $CodParc  |  operador $Usuario" -ForegroundColor Yellow

# --- 0a) escrita sem sessão tem que ser recusada ---------------------------
Passo 0a "POST /chamadas/iniciar SEM token -> deve dar 401"
$semAuth = Chamar POST "/api/cobranca/chamadas/iniciar" `
  @{ codParc = $CodParc; nufins = @(1); sentido = "PROATIVA" } -SemToken
if ($semAuth.status -eq 401) { Ok "401: $($semAuth.corpo.erro)" } else { Falha "esperava 401, veio $($semAuth.status)" }

# --- 0b) login -------------------------------------------------------------
Passo 0b "POST /login"
$login = Chamar POST "/api/cobranca/login" @{ usuario = $Usuario; senha = $Senha }
if ($login.status -ne 200 -or -not $login.corpo.token) {
  Falha "login falhou ($($login.status)): $($login.corpo.erro)"; exit 1
}
$Token = $login.corpo.token
$CodUsu = [int]$login.corpo.codUsu
Ok "$($login.corpo.nomeUsu) (codUsu $CodUsu), sessao de $($login.corpo.expiraEmHoras)h"

Passo 0c "Token adulterado -> deve dar 401"
$TokenBom = $Token
$Token = $Token.Substring(0, $Token.Length - 2) + "xx"
$adulterado = Chamar POST "/api/cobranca/chamadas/iniciar" `
  @{ codParc = $CodParc; nufins = @(1); sentido = "PROATIVA" }
if ($adulterado.status -eq 401) { Ok "assinatura invalida recusada" } else { Falha "esperava 401, veio $($adulterado.status)" }
$Token = $TokenBom

# --- 0d) escolhe DOIS títulos do cliente -----------------------------------
Passo 0d "Buscando titulos em aberto do cliente"
$extrato = Chamar POST "/api/cobranca/extrato" @{ codParc = $CodParc }
if ($extrato.status -ne 200 -or -not $extrato.corpo.dados) {
  Falha "cliente sem titulos em aberto (ou API fora): $($extrato.corpo.erro)"; exit 1
}
$titulos = @($extrato.corpo.dados | Select-Object -First 2)
$nufins = @($titulos | ForEach-Object { [int]$_.nuFin })
if ($nufins.Count -lt 2) { Falha "cliente tem so 1 titulo; o teste de multi-titulo nao roda"; exit 1 }
Ok "NUFINs de teste: $($nufins -join ', ')"

# --- 1) iniciar (2 titulos) ------------------------------------------------
Passo 1 "POST /chamadas/iniciar (PROATIVA, 2 titulos)"
$r = Chamar POST "/api/cobranca/chamadas/iniciar" `
  @{ codParc = $CodParc; nufins = $nufins; sentido = "PROATIVA" }
if ($r.status -ne 201) { Falha "esperava 201, veio $($r.status): $($r.corpo.erro)"; exit 1 }
$cod = [int]$r.corpo.codChamada
$criadas += $cod
$dur = ([datetime]$r.corpo.dhExpira - [datetime]$r.corpo.dhInicio).TotalMinutes
Ok "codChamada=$cod  trava de $dur min (esperado 15)"
if ([math]::Abs($dur - 15) -gt 0.5) { Falha "duracao da trava fora do esperado: $dur min" }

# --- 2) trava dura (sequencial) --------------------------------------------
Passo 2 "Outro operador tenta o mesmo titulo -> deve dar 409"
$r2 = Chamar POST "/api/cobranca/chamadas/iniciar" `
  @{ codParc = $CodParc; nufins = @($nufins[0]); sentido = "PROATIVA" }
if ($r2.status -eq 409 -and $r2.corpo.nufinsTravados) {
  Ok "409: $($r2.corpo.erro) (travado desde $($r2.corpo.nufinsTravados[0].desde))"
} else { Falha "esperava 409 com nufinsTravados, veio $($r2.status)" }

# --- 3) corrida real: dois /iniciar em PARALELO ----------------------------
# Unico passo que exercita o SELECT ... FOR UPDATE WAIT 5 na TGFFIN. Os dois
# titulos estao travados pelo passo 1, entao a corrida so roda depois de liberar.
Passo 3 "Liberando a chamada $cod para poder testar a corrida"
$r3 = Chamar POST "/api/cobranca/chamadas/$cod/cancelar"
if ($r3.status -eq 200) { Ok "cancelada (trava liberada)" } else { Falha "cancelar veio $($r3.status): $($r3.corpo.erro)" }

Passo 3.1 "Cancelar de novo -> deve ser idempotente (200)"
$r31 = Chamar POST "/api/cobranca/chamadas/$cod/cancelar"
if ($r31.status -eq 200) { Ok "idempotente" } else { Falha "esperava 200, veio $($r31.status)" }

Passo 3.2 "Dois /iniciar SIMULTANEOS no mesmo titulo -> 1x201 e 1x409"
$bloco = {
  param($uri, $json, $tk)
  try {
    $resp = Invoke-WebRequest -Uri $uri -Method POST -ContentType "application/json" `
      -Headers @{ Authorization = "Bearer $tk" } -Body $json -UseBasicParsing -TimeoutSec 60
    return "$([int]$resp.StatusCode)|$($resp.Content)"
  } catch {
    $r = $_.Exception.Response
    if ($null -eq $r) { return "ERRO|$($_.Exception.Message)" }
    $t = $_.ErrorDetails.Message
    if (-not $t) { try { $t = (New-Object System.IO.StreamReader($r.GetResponseStream())).ReadToEnd() } catch { $t = "" } }
    return "$([int]$r.StatusCode)|$t"
  }
}
$json = @{ codParc = $CodParc; nufins = @($nufins[0]); sentido = "PROATIVA" } | ConvertTo-Json -Depth 5
$uri = "$Base/api/cobranca/chamadas/iniciar"
$j1 = Start-Job -ScriptBlock $bloco -ArgumentList $uri, $json, $Token
$j2 = Start-Job -ScriptBlock $bloco -ArgumentList $uri, $json, $Token
$res = @(Receive-Job -Job (Wait-Job -Job $j1, $j2))
Remove-Job -Job $j1, $j2
$codigos = @($res | ForEach-Object { ($_ -split "\|", 2)[0] })
Write-Host "    respostas: $($codigos -join ' e ')"
if (($codigos | Where-Object { $_ -eq "201" }).Count -eq 1 -and ($codigos | Where-Object { $_ -eq "409" }).Count -eq 1) {
  Ok "corrida serializada corretamente"
} else { Falha "esperava um 201 e um 409, veio: $($codigos -join ', ')" }
foreach ($linha in $res) {
  $partes = $linha -split "\|", 2
  if ($partes[0] -eq "201") { $criadas += [int](($partes[1] | ConvertFrom-Json).codChamada) }
}
# limpa a chamada vencedora da corrida para seguir o teste
$vencedora = $criadas[-1]
Chamar POST "/api/cobranca/chamadas/$vencedora/cancelar" | Out-Null

# --- 4) chamada de verdade, com os 2 titulos -------------------------------
Passo 4 "POST /chamadas/iniciar (a chamada que sera finalizada)"
$r4 = Chamar POST "/api/cobranca/chamadas/iniciar" `
  @{ codParc = $CodParc; nufins = $nufins; sentido = "PROATIVA" }
if ($r4.status -ne 201) { Falha "esperava 201, veio $($r4.status): $($r4.corpo.erro)"; exit 1 }
$cod = [int]$r4.corpo.codChamada
$criadas += $cod
Ok "codChamada=$cod"

Passo 4.1 "GET /locks -> os 2 titulos travados"
$r41 = Chamar GET "/api/cobranca/locks?nufins=$($nufins -join ',')"
if ($r41.corpo.totalRegistros -eq 2) { Ok "2 travas, por $($r41.corpo.dados[0].nomeUsu)" }
else { Falha "esperava 2 travas, veio $($r41.corpo.totalRegistros)" }

Passo 4.2 "PUT /renovar (heartbeat)"
$r42 = Chamar PUT "/api/cobranca/chamadas/$cod/renovar"
if ($r42.status -eq 200) { Ok "nova expiracao: $($r42.corpo.dhExpira)" } else { Falha "renovar veio $($r42.status): $($r42.corpo.erro)" }

# --- 5) anexos -------------------------------------------------------------
Passo 5 "POST /anexos (link https)"
$r5 = Chamar POST "/api/cobranca/chamadas/$cod/anexos" `
  @{ url = "https://drive.exemplo.com/boleto-teste.pdf"; descricao = "Boleto teste" }
if ($r5.status -eq 201) { Ok "codAnexo=$($r5.corpo.codAnexo)" } else { Falha "esperava 201, veio $($r5.status): $($r5.corpo.erro)" }

Passo 5.1 "POST /anexos com javascript: -> 400"
$r51 = Chamar POST "/api/cobranca/chamadas/$cod/anexos" @{ url = "javascript:alert(1)" }
if ($r51.status -eq 400) { Ok "400: $($r51.corpo.erro)" } else { Falha "esperava 400, veio $($r51.status)" }

# --- 6) finalizar com desfecho DIFERENTE por titulo ------------------------
Passo 6 "PUT /finalizar (ACORDO no 1o titulo, SEM_ACORDO no 2o)"
$r6 = Chamar PUT "/api/cobranca/chamadas/$cod/finalizar" @{
  status = "ATENDEU"; resumo = "Teste automatizado da regua."
  itens  = @(@{ nufin = $nufins[0]; desfecho = "ACORDO" }, @{ nufin = $nufins[1]; desfecho = "SEM_ACORDO" })
}
if ($r6.status -ne 200) { Falha "esperava 200, veio $($r6.status): $($r6.corpo.erro)"; exit 1 }
$r6.corpo.itens | Format-Table nufin, ordem, desfecho -AutoSize | Out-String | Write-Host
$ordem1 = [int]($r6.corpo.itens[0].ordem)
if (($r6.corpo.itens | Where-Object { $_.ordem -ne 1 }).Count -eq 0) { Ok "ordem = 1 nos dois titulos" }
else { Falha "ordem inesperada" }
$d1 = ($r6.corpo.itens | Where-Object { $_.nufin -eq $nufins[0] }).desfecho
$d2 = ($r6.corpo.itens | Where-Object { $_.nufin -eq $nufins[1] }).desfecho
if ($d1 -eq "ACORDO" -and $d2 -eq "SEM_ACORDO") { Ok "desfecho gravado por titulo, independente" }
else { Falha "desfechos trocados/perdidos: $d1 / $d2" }

Passo 6.1 "GET /locks -> trava liberada"
$r61 = Chamar GET "/api/cobranca/locks?nufins=$($nufins -join ',')"
if ($r61.corpo.totalRegistros -eq 0) { Ok "sem travas" } else { Falha "ainda ha $($r61.corpo.totalRegistros) trava(s)" }

Passo 6.2 "PUT /finalizar de novo -> 409 (regua nao conta duas vezes)"
$r62 = Chamar PUT "/api/cobranca/chamadas/$cod/finalizar" @{ status = "ATENDEU"; itens = @() }
if ($r62.status -eq 409) { Ok "409: $($r62.corpo.erro)" } else { Falha "esperava 409, veio $($r62.status)" }

# --- 7) regua + historico --------------------------------------------------
Passo 7 "GET /regua (do cliente)"
$r7 = Chamar GET "/api/cobranca/regua?codParc=$CodParc"
$linha = $r7.corpo.dados | Where-Object { $_.nufin -eq $nufins[0] }
if ($linha -and $linha.ordemAtual -eq 1 -and -not $linha.podeJuridico) {
  Ok "ordemAtual=$($linha.ordemAtual)  ultimoDesfecho=$($linha.ultimoDesfecho)  podeJuridico=$($linha.podeJuridico)"
} else { Falha "regua inesperada: $($linha | ConvertTo-Json -Compress)" }

Passo 7.1 "GET /regua SEM codParc -> carteira inteira, com o titulo dentro"
$r71 = Chamar GET "/api/cobranca/regua"
$geral = $r71.corpo.dados | Where-Object { $_.nufin -eq $nufins[0] }
if ($geral -and $geral.codParc -eq $CodParc) {
  Ok "$($r71.corpo.totalRegistros) titulo(s) na regua da carteira"
} else { Falha "titulo nao apareceu na regua geral" }

Passo 7.2 "GET /chamadas (historico com itens e anexos, URL vem de CLOB)"
$r72 = Chamar GET "/api/cobranca/chamadas?codParc=$CodParc"
$hist = $r72.corpo.dados | Where-Object { $_.codChamada -eq $cod }
if ($hist -and $hist.itens.Count -eq 2 -and $hist.anexos.Count -eq 1 -and $hist.anexos[0].url -like "https://*") {
  Ok "chamada ${cod}: $($hist.itens.Count) itens, anexo -> $($hist.anexos[0].url)"
} else { Falha "historico incompleto: $($hist | ConvertTo-Json -Depth 4 -Compress)" }

# --- 8) receptiva NAO conta na regua ---------------------------------------
Passo 8 "Chamada RECEPTIVA -> ordem null e regua nao sobe"
$r8 = Chamar POST "/api/cobranca/chamadas/iniciar" `
  @{ codParc = $CodParc; nufins = @($nufins[0]); sentido = "RECEPTIVA" }
if ($r8.status -ne 201) { Falha "iniciar receptiva veio $($r8.status): $($r8.corpo.erro)" }
else {
  $cod8 = [int]$r8.corpo.codChamada
  $criadas += $cod8
  $r81 = Chamar PUT "/api/cobranca/chamadas/$cod8/finalizar" `
    @{ status = "CAIXA_POSTAL"; itens = @(@{ nufin = $nufins[0]; desfecho = "EM_ABERTO" }) }
  if ($null -eq $r81.corpo.itens[0].ordem) { Ok "ordem = null" } else { Falha "receptiva incrementou a ordem!" }
  $r82 = Chamar GET "/api/cobranca/regua?codParc=$CodParc"
  $l2 = $r82.corpo.dados | Where-Object { $_.nufin -eq $nufins[0] }
  if ($l2.ordemAtual -eq $ordem1) { Ok "regua continua em $($l2.ordemAtual)" } else { Falha "regua subiu para $($l2.ordemAtual)" }
}

# --- 9) validacoes de payload ----------------------------------------------
Passo 9 "Payload invalido"
$v1 = Chamar POST "/api/cobranca/chamadas/iniciar" @{ codParc = $CodParc; nufins = $nufins; sentido = "LIGACAO" }
if ($v1.status -eq 400) { Ok "sentido fora do dominio -> 400" } else { Falha "sentido invalido veio $($v1.status)" }
$v2 = Chamar POST "/api/cobranca/chamadas/iniciar" @{ codParc = $CodParc; nufins = @(999999999); sentido = "PROATIVA" }
if ($v2.status -eq 404) { Ok "titulo inexistente -> 404" } else { Falha "titulo inexistente veio $($v2.status)" }
$v3 = Chamar PUT "/api/cobranca/chamadas/$cod/finalizar" @{ status = "AGENDOU"; itens = @() }
if ($v3.status -eq 400 -or $v3.status -eq 409) { Ok "AGENDOU sem dhAgenda -> $($v3.status)" } else { Falha "AGENDOU sem data veio $($v3.status)" }

# --- resumo ----------------------------------------------------------------
$ids = ($criadas | Sort-Object -Unique) -join ", "
Write-Host "`n===========================================================" -ForegroundColor Yellow
if ($falhas -eq 0) { Write-Host "TUDO PASSOU" -ForegroundColor Green }
else { Write-Host "$falhas VERIFICACAO(OES) FALHARAM" -ForegroundColor Red }
Write-Host "Chamadas criadas: $ids"
Write-Host "`nSQL de limpeza (rodar no Sankhya):" -ForegroundColor Yellow
Write-Host @"
DELETE FROM AD_COBRANEXO       WHERE CODCHAMADA IN ($ids);
DELETE FROM AD_COBRCHAMADAITEM WHERE CODCHAMADA IN ($ids);
DELETE FROM AD_COBRCHAMADA     WHERE CODCHAMADA IN ($ids);
COMMIT;
"@
