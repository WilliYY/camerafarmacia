@echo off
:: Garante codificacao UTF-8 para exibir acentos corretamente
chcp 65001 > nul
echo ========================================================
echo   LIBERAR PORTAS DA CÂMERA NO FIREWALL (REDE LOCAL)
echo ========================================================
echo.
echo Este script precisa ser executado como ADMINISTRADOR.
echo Se você não executou como administrador, feche este arquivo,
echo clique com o botão direito nele e escolha "Executar como Administrador".
echo.
echo Pressione qualquer tecla para continuar e liberar as portas...
pause > nul
echo.
echo [+] Liberando porta 1984 (Navegador/Web UI)...
powershell -Command "New-NetFirewallRule -DisplayName 'Camera Farmacia - API (1984)' -Direction Inbound -LocalPort 1984 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue"
echo [+] Liberando porta 8554 (RTSP/VLC)...
powershell -Command "New-NetFirewallRule -DisplayName 'Camera Farmacia - RTSP (8554)' -Direction Inbound -LocalPort 8554 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue"
echo.
echo ========================================================
echo   Portas 1984 e 8554 liberadas no Firewall do Windows!
echo   Agora outros dispositivos na rede local podem acessar.
echo ========================================================
echo.
pause
