@echo off
chcp 65001 >nul
title 刷新 Windows 图标缓存
echo.
echo   安装守卫 - 刷新图标缓存
echo   ========================
echo.
echo   如果 exe 图标显示为旧的黄色图标，运行本脚本可强制刷新。
echo   过程中任务栏会消失几秒后自动恢复，属正常现象。
echo.
pause

echo 正在结束资源管理器...
taskkill /f /im explorer.exe >nul 2>&1

echo 正在清理图标缓存...
del /a /q "%LocalAppData%\IconCache.db" >nul 2>&1
del /a /f /q "%LocalAppData%\Microsoft\Windows\Explorer\iconcache_*.db" >nul 2>&1
if exist "%LocalAppData%\Microsoft\Windows\Explorer\thumbcache_*.db" del /a /f /q "%LocalAppData%\Microsoft\Windows\Explorer\thumbcache_*.db" >nul 2>&1

echo 正在重启资源管理器...
start explorer.exe

echo.
echo 完成！请重新打开 exe 所在文件夹查看图标。
echo 若仍显示旧图标，请确认使用的是最新版本 InstallGuard.exe
echo （右键 exe - 属性 - 详细信息，文件大小约 17.3 MB）。
echo.
pause
