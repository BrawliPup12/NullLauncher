from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import hashlib
import html
import io
from html.parser import HTMLParser
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
import unicodedata
import webbrowser
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Optional

APP_NAME = "NullLauncher"
APP_VERSION = "1.11.0"
REQUIRED_MLL = "8.0"
REQUIRED_PILLOW = "10.0"
MIN_PYTHON = (3, 10)

UPDATE_REPO_OWNER = "BrawliPup12"
UPDATE_REPO_NAME = "NullLauncher"
UPDATE_REPO_URL = f"https://github.com/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}"
UPDATE_API_LATEST = f"https://api.github.com/repos/{UPDATE_REPO_OWNER}/{UPDATE_REPO_NAME}/releases/latest"
UPDATE_MAX_BYTES = 128 * 1024 * 1024

BIG_LOGO = [
    r" _   _       _ _ _                           _               ",
    r"| \ | |_   _| | | |    __ _ _   _ _ __   ___| |__   ___ _ __ ",
    r"|  \| | | | | | | |   / _` | | | | '_ \ / __| '_ \ / _ \ '__|",
    r"| |\  | |_| | | | |__| (_| | |_| | | | | (__| | | |  __/ |   ",
    r"|_| \_|\__,_|_|_|_____\__,_|\__,_|_| |_|\___|_| |_|\___|_|   ",
]

SMALL_LOGO = "NULLLAUNCHER"
SPLASH_LINES = {
    "en": ["Nothing extra. Just launch.", "CMD is an interface too.", "Vanilla inside. Null outside.", "Keyboard first. Mouse friendly.", "Less shell. More Minecraft."],
    "ru": ["Ноль лишнего. Только запуск.", "CMD — тоже интерфейс.", "Vanilla внутри. Null снаружи.", "Клавиатура прежде всего. Мышь тоже понимает.", "Меньше оболочки — больше Minecraft."],
    "uk": ["Нічого зайвого. Лише запуск.", "CMD — теж інтерфейс.", "Vanilla всередині. Null зовні.", "Клавіатура першою. Миша теж працює.", "Менше оболонки — більше Minecraft."],
    "be": ["Нічога лішняга. Толькі запуск.", "CMD — таксама інтэрфейс.", "Vanilla ўнутры. Null звонку.", "Клавіятура першай. Мыш таксама працуе.", "Менш абалонкі — больш Minecraft."],
}

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MC_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[92m"
CYAN = "\x1b[96m"
YELLOW = "\x1b[93m"
RED = "\x1b[91m"
GRAY = "\x1b[90m"
WHITE = "\x1b[97m"

DEFAULT_THEME = {
    "theme_primary": "#62F4FF",
    "theme_subtitle": "#9AA4AD",
    "theme_status": "#737D86",
    "news_filter_color": "#62F4FF",
}

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "minecraft_locale": "en-us", "translate": "en"},
    "ru": {"name": "Русский", "minecraft_locale": "ru-ru", "translate": "ru"},
    "uk": {"name": "Українська", "minecraft_locale": "en-us", "translate": "uk"},
    "be": {"name": "Беларуская", "minecraft_locale": "en-us", "translate": "be"},
    "pl": {"name": "Polski", "minecraft_locale": "pl-pl", "translate": "pl"},
    "de": {"name": "Deutsch", "minecraft_locale": "de-de", "translate": "de"},
    "fr": {"name": "Français", "minecraft_locale": "fr-fr", "translate": "fr"},
    "es": {"name": "Español", "minecraft_locale": "es-es", "translate": "es"},
    "pt-BR": {"name": "Português (Brasil)", "minecraft_locale": "pt-br", "translate": "pt"},
    "zh-CN": {"name": "简体中文", "minecraft_locale": "zh-hans", "translate": "zh-CN"},
    "ja": {"name": "日本語", "minecraft_locale": "en-us", "translate": "ja"},
    "ko": {"name": "한국어", "minecraft_locale": "en-us", "translate": "ko"},
}

I18N: dict[str, dict[str, str]] = {
    "en": {
        "play":"Play","versions":"Versions","accounts":"Accounts","proxies":"Proxy connections","settings":"Launcher settings","exit":"Exit","news":"Minecraft News","back":"Back",
        "account":"Account","version":"Version","proxy":"Proxy","none":"none","not_selected":"not selected","yes":"yes","no":"no",
        "footer":"↑↓ / wheel — select   Enter / click — open   Esc — back","footer_main":"↑↓ / wheel — select   Enter / click — open",
        "create_account":"Create offline account","account_section":"accounts","select_launch":"Select for launch","delete":"Delete",
        "create_proxy":"Create proxy profile","direct":"Direct connection","proxy_section":"proxies","disable_proxy":"Disable proxy","type_proxy":"Proxy type",
        "search":"Search","filter":"Filter","all":"All","installed":"Installed","available":"Available","catalog_summary":"{count} versions · ✓ installed · ★ selected",
        "download":"Download","version_settings":"Version settings","source":"Source",
        "open_browser":"Open article in browser","back_news":"Back to news","no_cover":"This article has no available cover image.","no_description":"No short description is available for this article.",
        "sixel_required":"Image preview requires a SIXEL-capable terminal (for example Windows Terminal 1.22+).",
        "settings_paths":"Paths & runtime","settings_catalog":"Catalog & content","settings_interface":"Interface","settings_launch":"Launch behavior","settings_actions":"Actions",
        "minecraft_dir":"Minecraft folder","show_snapshots":"Show snapshots","show_old":"Show Alpha/Beta","load_news":"Load Minecraft news","mouse_menu":"Mouse in menus","repair":"Verify files before launch","close_after":"Close launcher after game starts","default_ram":"Default RAM","java_manual":"Java path","open_data":"Open NullLauncher folder","reset_defaults":"Reset to defaults",
        "language":"Language","primary_color":"Primary color","subtitle_color":"Subtitle color","status_color":"Status line color","after_restart":"after restart","auto_runtime":"auto (Mojang runtime)",
        "version_memory":"Memory","version_window":"Window","version_files":"Files","version_actions":"Actions","ram_min":"Minimum RAM","ram_max":"Maximum RAM","custom_resolution":"Custom resolution","window_size":"Window size","separate_dir":"Separate game folder",
        "loading_news":"Loading localized Minecraft news","translating_article":"Translating article","color_prompt":"Enter #RRGGBB (or R,G,B)","language_title":"Launcher language",
        "reset_confirm":"Restore NullLauncher settings to defaults? Accounts and installed versions will not be changed.","version_reset_confirm":"Restore this version's settings to defaults?"
    },
    "ru": {
        "play":"Играть","versions":"Версии","accounts":"Аккаунты","proxies":"Прокси подключения","settings":"Настройки лаунчера","exit":"Выйти","news":"Новости Minecraft","back":"Назад",
        "account":"Аккаунт","version":"Версия","proxy":"Прокси","none":"нет","not_selected":"не выбрана","yes":"да","no":"нет",
        "footer":"↑↓ / колесо — выбор   Enter / клик — открыть   Esc — назад","footer_main":"↑↓ / колесо — выбор   Enter / клик — открыть",
        "create_account":"Создать offline-аккаунт","account_section":"аккаунты","select_launch":"Выбрать для запуска","delete":"Удалить",
        "create_proxy":"Создать прокси-профиль","direct":"Прямое подключение","proxy_section":"прокси","disable_proxy":"Отключить прокси","type_proxy":"Тип прокси",
        "search":"Поиск","filter":"Фильтр","all":"Все","installed":"Установленные","available":"Доступные","catalog_summary":"{count} версий · ✓ установлена · ★ выбрана",
        "download":"Скачать","version_settings":"Настройки версии","source":"Источник",
        "open_browser":"Открыть статью в браузере","back_news":"Назад к новостям","no_cover":"У этой статьи нет доступной обложки.","no_description":"Нет краткого описания для этой статьи.",
        "sixel_required":"Превью изображения требует терминал с поддержкой SIXEL (например Windows Terminal 1.22+).",
        "settings_paths":"Пути и Java","settings_catalog":"Каталог и контент","settings_interface":"Интерфейс","settings_launch":"Запуск игры","settings_actions":"Действия",
        "minecraft_dir":"Папка Minecraft","show_snapshots":"Snapshots в каталоге","show_old":"Alpha/Beta в каталоге","load_news":"Загружать новости Minecraft","mouse_menu":"Мышь в меню","repair":"Проверять файлы перед запуском","close_after":"Закрывать лаунчер после старта игры","default_ram":"RAM по умолчанию","java_manual":"Java вручную","open_data":"Открыть папку NullLauncher","reset_defaults":"Сбросить по умолчанию",
        "language":"Язык","primary_color":"Основной цвет","subtitle_color":"Цвет подписей","status_color":"Цвет строки состояния","after_restart":"после перезапуска","auto_runtime":"auto (Mojang runtime)",
        "version_memory":"Память","version_window":"Окно","version_files":"Файлы","version_actions":"Действия","ram_min":"RAM минимум","ram_max":"RAM максимум","custom_resolution":"Свое разрешение","window_size":"Размер окна","separate_dir":"Отдельная папка игры",
        "loading_news":"Загружаю локализованные новости Minecraft","translating_article":"Перевожу статью","color_prompt":"Введите #RRGGBB (или R,G,B)","language_title":"Язык лаунчера",
        "reset_confirm":"Вернуть настройки NullLauncher по умолчанию? Аккаунты и установленные версии останутся без изменений.","version_reset_confirm":"Вернуть настройки этой версии к значениям по умолчанию?"
    },
    "uk": {"play":"Грати","versions":"Версії","accounts":"Акаунти","proxies":"Проксі-підключення","settings":"Налаштування лаунчера","exit":"Вийти","news":"Новини Minecraft","back":"Назад","account":"Акаунт","version":"Версія","proxy":"Проксі","none":"немає","not_selected":"не вибрано","yes":"так","no":"ні","create_account":"Створити offline-акаунт","select_launch":"Вибрати для запуску","delete":"Видалити","create_proxy":"Створити проксі-профіль","direct":"Пряме підключення","search":"Пошук","filter":"Фільтр","all":"Усі","installed":"Встановлені","available":"Доступні","open_browser":"Відкрити статтю в браузері","back_news":"Назад до новин","settings_interface":"Інтерфейс","language":"Мова","primary_color":"Основний колір","subtitle_color":"Колір підписів","status_color":"Колір рядка стану","reset_defaults":"Скинути до стандартних","language_title":"Мова лаунчера"},
    "be": {"play":"Гуляць","versions":"Версіі","accounts":"Акаўнты","proxies":"Проксі-злучэнні","settings":"Налады лаунчара","exit":"Выйсці","news":"Навіны Minecraft","back":"Назад","account":"Акаўнт","version":"Версія","proxy":"Проксі","none":"няма","not_selected":"не выбрана","yes":"так","no":"не","create_account":"Стварыць offline-акаўнт","select_launch":"Выбраць для запуску","delete":"Выдаліць","create_proxy":"Стварыць проксі-профіль","direct":"Прамое злучэнне","search":"Пошук","filter":"Фільтр","all":"Усе","installed":"Усталяваныя","available":"Даступныя","open_browser":"Адкрыць артыкул у браўзеры","back_news":"Назад да навін","settings_interface":"Інтэрфейс","language":"Мова","primary_color":"Асноўны колер","subtitle_color":"Колер подпісаў","status_color":"Колер радка стану","reset_defaults":"Скінуць па змаўчанні","language_title":"Мова лаунчара"},
    "pl": {"play":"Graj","versions":"Wersje","accounts":"Konta","proxies":"Połączenia proxy","settings":"Ustawienia launchera","exit":"Wyjdź","news":"Aktualności Minecraft","back":"Wstecz","language":"Język","primary_color":"Kolor główny","subtitle_color":"Kolor podpisów","status_color":"Kolor paska stanu","installed":"Zainstalowane","available":"Dostępne","search":"Szukaj","filter":"Filtr","reset_defaults":"Przywróć domyślne"},
    "de": {"play":"Spielen","versions":"Versionen","accounts":"Konten","proxies":"Proxy-Verbindungen","settings":"Launcher-Einstellungen","exit":"Beenden","news":"Minecraft-News","back":"Zurück","language":"Sprache","primary_color":"Hauptfarbe","subtitle_color":"Untertitelfarbe","status_color":"Statuszeilenfarbe","installed":"Installiert","available":"Verfügbar","search":"Suche","filter":"Filter","reset_defaults":"Auf Standard zurücksetzen"},
    "fr": {"play":"Jouer","versions":"Versions","accounts":"Comptes","proxies":"Connexions proxy","settings":"Paramètres du launcher","exit":"Quitter","news":"Actualités Minecraft","back":"Retour","language":"Langue","primary_color":"Couleur principale","subtitle_color":"Couleur des sous-titres","status_color":"Couleur de la barre d'état","installed":"Installées","available":"Disponibles","search":"Recherche","filter":"Filtre","reset_defaults":"Réinitialiser"},
    "es": {"play":"Jugar","versions":"Versiones","accounts":"Cuentas","proxies":"Conexiones proxy","settings":"Ajustes del launcher","exit":"Salir","news":"Noticias de Minecraft","back":"Atrás","language":"Idioma","primary_color":"Color principal","subtitle_color":"Color de subtítulos","status_color":"Color de barra de estado","installed":"Instaladas","available":"Disponibles","search":"Buscar","filter":"Filtro","reset_defaults":"Restablecer valores"},
    "pt-BR": {"play":"Jogar","versions":"Versões","accounts":"Contas","proxies":"Conexões proxy","settings":"Configurações do launcher","exit":"Sair","news":"Notícias do Minecraft","back":"Voltar","language":"Idioma","primary_color":"Cor principal","subtitle_color":"Cor das legendas","status_color":"Cor da barra de status","installed":"Instaladas","available":"Disponíveis","search":"Buscar","filter":"Filtro","reset_defaults":"Restaurar padrões"},
    "zh-CN": {"play":"开始游戏","versions":"版本","accounts":"账户","proxies":"代理连接","settings":"启动器设置","exit":"退出","news":"Minecraft 新闻","back":"返回","language":"语言","primary_color":"主色","subtitle_color":"说明文字颜色","status_color":"状态栏颜色","installed":"已安装","available":"可用版本","search":"搜索","filter":"筛选","reset_defaults":"恢复默认设置"},
    "ja": {"play":"プレイ","versions":"バージョン","accounts":"アカウント","proxies":"プロキシ接続","settings":"ランチャー設定","exit":"終了","news":"Minecraft ニュース","back":"戻る","language":"言語","primary_color":"メインカラー","subtitle_color":"サブテキスト色","status_color":"ステータス行の色","installed":"インストール済み","available":"利用可能","search":"検索","filter":"フィルター","reset_defaults":"デフォルトに戻す"},
    "ko": {"play":"플레이","versions":"버전","accounts":"계정","proxies":"프록시 연결","settings":"런처 설정","exit":"종료","news":"Minecraft 뉴스","back":"뒤로","language":"언어","primary_color":"기본 색상","subtitle_color":"보조 텍스트 색상","status_color":"상태 표시줄 색상","installed":"설치됨","available":"사용 가능","search":"검색","filter":"필터","reset_defaults":"기본값으로 재설정"},
}

                                                            
I18N["uk"].update({
    "footer":"↑↓ / колесо — вибір   Enter / клік — відкрити   Esc — назад","footer_main":"↑↓ / колесо — вибір   Enter / клік — відкрити","account_section":"акаунти","proxy_section":"проксі","disable_proxy":"Вимкнути проксі","type_proxy":"Тип проксі","catalog_summary":"{count} версій · ✓ встановлено · ★ вибрано","download":"Завантажити","version_settings":"Налаштування версії","source":"Джерело","no_cover":"Для цієї статті немає доступної обкладинки.","no_description":"Короткого опису немає.","sixel_required":"Для зображення потрібен термінал із підтримкою SIXEL (наприклад Windows Terminal 1.22+).",
    "settings_paths":"Шляхи та Java","settings_catalog":"Каталог і контент","settings_launch":"Запуск гри","settings_actions":"Дії","minecraft_dir":"Папка Minecraft","show_snapshots":"Показувати snapshots","show_old":"Показувати Alpha/Beta","load_news":"Завантажувати новини Minecraft","mouse_menu":"Миша в меню","repair":"Перевіряти файли перед запуском","close_after":"Закривати лаунчер після запуску гри","default_ram":"RAM за замовчуванням","java_manual":"Шлях Java","open_data":"Відкрити папку NullLauncher","after_restart":"після перезапуску","auto_runtime":"auto (Mojang runtime)",
    "version_memory":"Пам'ять","version_window":"Вікно","version_files":"Файли","version_actions":"Дії","ram_min":"Мінімум RAM","ram_max":"Максимум RAM","custom_resolution":"Власна роздільність","window_size":"Розмір вікна","separate_dir":"Окрема папка гри","loading_news":"Завантаження локалізованих новин Minecraft","translating_article":"Переклад статті","color_prompt":"Введіть #RRGGBB (або R,G,B)","reset_confirm":"Повернути стандартні налаштування NullLauncher? Акаунти та версії залишаться.","version_reset_confirm":"Повернути стандартні налаштування цієї версії?"
})
I18N["be"].update({
    "footer":"↑↓ / кола — выбар   Enter / клік — адкрыць   Esc — назад","footer_main":"↑↓ / кола — выбар   Enter / клік — адкрыць","account_section":"акаўнты","proxy_section":"проксі","disable_proxy":"Адключыць проксі","type_proxy":"Тып проксі","catalog_summary":"{count} версій · ✓ усталявана · ★ выбрана","download":"Спампаваць","version_settings":"Налады версіі","source":"Крыніца","no_cover":"Для гэтага артыкула няма даступнай вокладкі.","no_description":"Кароткага апісання няма.","sixel_required":"Для выявы патрэбны тэрмінал з падтрымкай SIXEL (напрыклад Windows Terminal 1.22+).",
    "settings_paths":"Шляхі і Java","settings_catalog":"Каталог і кантэнт","settings_launch":"Запуск гульні","settings_actions":"Дзеянні","minecraft_dir":"Папка Minecraft","show_snapshots":"Паказваць snapshots","show_old":"Паказваць Alpha/Beta","load_news":"Загружаць навіны Minecraft","mouse_menu":"Мыш у меню","repair":"Правяраць файлы перад запускам","close_after":"Закрываць лаунчар пасля запуску гульні","default_ram":"RAM па змаўчанні","java_manual":"Шлях Java","open_data":"Адкрыць папку NullLauncher","after_restart":"пасля перазапуску","auto_runtime":"auto (Mojang runtime)",
    "version_memory":"Памяць","version_window":"Акно","version_files":"Файлы","version_actions":"Дзеянні","ram_min":"Мінімум RAM","ram_max":"Максімум RAM","custom_resolution":"Сваё разрозненне","window_size":"Памер акна","separate_dir":"Асобная папка гульні","loading_news":"Загрузка лакалізаваных навін Minecraft","translating_article":"Пераклад артыкула","color_prompt":"Увядзіце #RRGGBB (або R,G,B)","reset_confirm":"Вярнуць стандартныя налады NullLauncher? Акаўнты і версіі застануцца.","version_reset_confirm":"Вярнуць стандартныя налады гэтай версіі?"
})
I18N["pl"].update({"account":"Konto","version":"Wersja","proxy":"Proxy","none":"brak","not_selected":"nie wybrano","yes":"tak","no":"nie","footer":"↑↓ / kółko — wybór   Enter / klik — otwórz   Esc — wstecz","footer_main":"↑↓ / kółko — wybór   Enter / klik — otwórz","create_account":"Utwórz konto offline","account_section":"konta","select_launch":"Wybierz do uruchomienia","delete":"Usuń","create_proxy":"Utwórz profil proxy","direct":"Połączenie bezpośrednie","proxy_section":"proxy","disable_proxy":"Wyłącz proxy","type_proxy":"Typ proxy","all":"Wszystkie","catalog_summary":"{count} wersji · ✓ zainstalowana · ★ wybrana","download":"Pobierz","version_settings":"Ustawienia wersji","source":"Źródło","open_browser":"Otwórz artykuł w przeglądarce","back_news":"Wróć do aktualności","settings_paths":"Ścieżki i Java","settings_catalog":"Katalog i zawartość","settings_interface":"Interfejs","settings_launch":"Uruchamianie gry","settings_actions":"Akcje","minecraft_dir":"Folder Minecraft","show_snapshots":"Pokaż snapshoty","show_old":"Pokaż Alpha/Beta","load_news":"Ładuj aktualności Minecraft","mouse_menu":"Mysz w menu","repair":"Sprawdzaj pliki przed startem","close_after":"Zamknij launcher po starcie gry","default_ram":"Domyślna RAM","java_manual":"Ścieżka Java","open_data":"Otwórz folder NullLauncher","version_memory":"Pamięć","version_window":"Okno","version_files":"Pliki","version_actions":"Akcje","ram_min":"Minimalna RAM","ram_max":"Maksymalna RAM","custom_resolution":"Własna rozdzielczość","window_size":"Rozmiar okna","separate_dir":"Oddzielny folder gry","language_title":"Język launchera"})
I18N["de"].update({"account":"Konto","version":"Version","proxy":"Proxy","none":"keins","not_selected":"nicht gewählt","yes":"ja","no":"nein","create_account":"Offline-Konto erstellen","select_launch":"Zum Starten wählen","delete":"Löschen","create_proxy":"Proxy-Profil erstellen","direct":"Direkte Verbindung","disable_proxy":"Proxy deaktivieren","type_proxy":"Proxy-Typ","all":"Alle","catalog_summary":"{count} Versionen · ✓ installiert · ★ gewählt","download":"Herunterladen","version_settings":"Versionseinstellungen","source":"Quelle","open_browser":"Artikel im Browser öffnen","back_news":"Zurück zu den News","settings_paths":"Pfade & Java","settings_catalog":"Katalog & Inhalte","settings_interface":"Oberfläche","settings_launch":"Spielstart","settings_actions":"Aktionen","minecraft_dir":"Minecraft-Ordner","show_snapshots":"Snapshots anzeigen","show_old":"Alpha/Beta anzeigen","load_news":"Minecraft-News laden","mouse_menu":"Maus in Menüs","repair":"Dateien vor Start prüfen","close_after":"Launcher nach Spielstart schließen","default_ram":"Standard-RAM","java_manual":"Java-Pfad","open_data":"NullLauncher-Ordner öffnen","version_memory":"Speicher","version_window":"Fenster","version_files":"Dateien","version_actions":"Aktionen","ram_min":"Min. RAM","ram_max":"Max. RAM","custom_resolution":"Eigene Auflösung","window_size":"Fenstergröße","separate_dir":"Separater Spielordner","language_title":"Launcher-Sprache"})
I18N["fr"].update({"account":"Compte","version":"Version","proxy":"Proxy","none":"aucun","not_selected":"non sélectionnée","yes":"oui","no":"non","create_account":"Créer un compte hors ligne","select_launch":"Sélectionner pour lancer","delete":"Supprimer","create_proxy":"Créer un profil proxy","direct":"Connexion directe","disable_proxy":"Désactiver le proxy","type_proxy":"Type de proxy","all":"Tous","catalog_summary":"{count} versions · ✓ installée · ★ sélectionnée","download":"Télécharger","version_settings":"Paramètres de version","source":"Source","open_browser":"Ouvrir l'article dans le navigateur","back_news":"Retour aux actualités","settings_paths":"Chemins et Java","settings_catalog":"Catalogue et contenu","settings_interface":"Interface","settings_launch":"Lancement du jeu","settings_actions":"Actions","minecraft_dir":"Dossier Minecraft","show_snapshots":"Afficher les snapshots","show_old":"Afficher Alpha/Beta","load_news":"Charger les actualités Minecraft","mouse_menu":"Souris dans les menus","repair":"Vérifier les fichiers avant lancement","close_after":"Fermer le launcher au démarrage du jeu","default_ram":"RAM par défaut","java_manual":"Chemin Java","open_data":"Ouvrir le dossier NullLauncher","version_memory":"Mémoire","version_window":"Fenêtre","version_files":"Fichiers","version_actions":"Actions","ram_min":"RAM minimale","ram_max":"RAM maximale","custom_resolution":"Résolution personnalisée","window_size":"Taille de fenêtre","separate_dir":"Dossier de jeu séparé","language_title":"Langue du launcher"})
I18N["es"].update({"account":"Cuenta","version":"Versión","proxy":"Proxy","none":"ninguno","not_selected":"sin seleccionar","yes":"sí","no":"no","create_account":"Crear cuenta offline","select_launch":"Seleccionar para iniciar","delete":"Eliminar","create_proxy":"Crear perfil proxy","direct":"Conexión directa","disable_proxy":"Desactivar proxy","type_proxy":"Tipo de proxy","all":"Todas","catalog_summary":"{count} versiones · ✓ instalada · ★ seleccionada","download":"Descargar","version_settings":"Ajustes de versión","source":"Fuente","open_browser":"Abrir artículo en el navegador","back_news":"Volver a noticias","settings_paths":"Rutas y Java","settings_catalog":"Catálogo y contenido","settings_interface":"Interfaz","settings_launch":"Inicio del juego","settings_actions":"Acciones","minecraft_dir":"Carpeta de Minecraft","show_snapshots":"Mostrar snapshots","show_old":"Mostrar Alpha/Beta","load_news":"Cargar noticias de Minecraft","mouse_menu":"Ratón en menús","repair":"Verificar archivos antes de iniciar","close_after":"Cerrar launcher al iniciar el juego","default_ram":"RAM predeterminada","java_manual":"Ruta de Java","open_data":"Abrir carpeta de NullLauncher","version_memory":"Memoria","version_window":"Ventana","version_files":"Archivos","version_actions":"Acciones","ram_min":"RAM mínima","ram_max":"RAM máxima","custom_resolution":"Resolución personalizada","window_size":"Tamaño de ventana","separate_dir":"Carpeta de juego separada","language_title":"Idioma del launcher"})
I18N["pt-BR"].update({"account":"Conta","version":"Versão","proxy":"Proxy","none":"nenhum","not_selected":"não selecionada","yes":"sim","no":"não","create_account":"Criar conta offline","select_launch":"Selecionar para iniciar","delete":"Excluir","create_proxy":"Criar perfil proxy","direct":"Conexão direta","disable_proxy":"Desativar proxy","type_proxy":"Tipo de proxy","all":"Todas","catalog_summary":"{count} versões · ✓ instalada · ★ selecionada","download":"Baixar","version_settings":"Configurações da versão","source":"Fonte","open_browser":"Abrir artigo no navegador","back_news":"Voltar às notícias","settings_paths":"Caminhos e Java","settings_catalog":"Catálogo e conteúdo","settings_interface":"Interface","settings_launch":"Inicialização do jogo","settings_actions":"Ações","minecraft_dir":"Pasta do Minecraft","show_snapshots":"Mostrar snapshots","show_old":"Mostrar Alpha/Beta","load_news":"Carregar notícias do Minecraft","mouse_menu":"Mouse nos menus","repair":"Verificar arquivos antes de iniciar","close_after":"Fechar launcher após iniciar o jogo","default_ram":"RAM padrão","java_manual":"Caminho do Java","open_data":"Abrir pasta do NullLauncher","version_memory":"Memória","version_window":"Janela","version_files":"Arquivos","version_actions":"Ações","ram_min":"RAM mínima","ram_max":"RAM máxima","custom_resolution":"Resolução personalizada","window_size":"Tamanho da janela","separate_dir":"Pasta de jogo separada","language_title":"Idioma do launcher"})
I18N["zh-CN"].update({"account":"账户","version":"版本","proxy":"代理","none":"无","not_selected":"未选择","yes":"是","no":"否","create_account":"创建离线账户","select_launch":"选择用于启动","delete":"删除","create_proxy":"创建代理配置","direct":"直接连接","disable_proxy":"禁用代理","type_proxy":"代理类型","all":"全部","catalog_summary":"{count} 个版本 · ✓ 已安装 · ★ 已选择","download":"下载","version_settings":"版本设置","source":"来源","open_browser":"在浏览器中打开文章","back_news":"返回新闻","settings_paths":"路径与 Java","settings_catalog":"目录与内容","settings_interface":"界面","settings_launch":"游戏启动","settings_actions":"操作","minecraft_dir":"Minecraft 文件夹","show_snapshots":"显示快照","show_old":"显示 Alpha/Beta","load_news":"加载 Minecraft 新闻","mouse_menu":"菜单鼠标操作","repair":"启动前检查文件","close_after":"游戏启动后关闭启动器","default_ram":"默认 RAM","java_manual":"Java 路径","open_data":"打开 NullLauncher 文件夹","version_memory":"内存","version_window":"窗口","version_files":"文件","version_actions":"操作","ram_min":"最小 RAM","ram_max":"最大 RAM","custom_resolution":"自定义分辨率","window_size":"窗口大小","separate_dir":"独立游戏文件夹","language_title":"启动器语言"})


                                                                                
                                                                                
                  
_RUNTIME_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "preparing":"Preparing…","preload_local":"Reading local versions","preload_vanilla":"Loading Vanilla catalog","preload_loaders":"Loading Forge / NeoForge / Fabric / Quilt","preload_news":"Loading Minecraft news","preload_profiles":"Checking NullLauncher profiles","ready":"Ready","cache_warning":"Some network data was loaded from cache.",
        "prompt_help":"Type a value · Enter — save · Esc — cancel","do_not_close":"Do not close the terminal while files are being written.","news_unavailable":"Minecraft news is currently unavailable.","article":"Article","browser":"Browser","color_error":"Use #RRGGBB or R,G,B, for example 98,244,255.",
        "settings_language":"Language & appearance","settings_network":"Network & input","settings_storage":"Storage & runtime","settings_content":"Catalog & content","settings_game":"Game launch","settings_maintenance":"Maintenance",
    },
    "ru": {
        "preparing":"Подготовка…","preload_local":"Читаю локальные версии","preload_vanilla":"Загружаю список Vanilla","preload_loaders":"Загружаю Forge / NeoForge / Fabric / Quilt","preload_news":"Загружаю новости Minecraft","preload_profiles":"Проверяю профили NullLauncher","ready":"Готово","cache_warning":"Часть сетевых данных взята из кэша.",
        "prompt_help":"Введите значение · Enter — сохранить · Esc — отменить","do_not_close":"Не закрывайте терминал во время записи файлов.","news_unavailable":"Новости Minecraft сейчас недоступны.","article":"Статья","browser":"Браузер","color_error":"Используйте #RRGGBB или R,G,B, например 98,244,255.",
        "settings_language":"Язык и оформление","settings_network":"Сеть и ввод","settings_storage":"Хранилище и Java","settings_content":"Каталог и контент","settings_game":"Запуск игры","settings_maintenance":"Обслуживание",
    },
    "uk": {
        "preparing":"Підготовка…","preload_local":"Читаю локальні версії","preload_vanilla":"Завантажую каталог Vanilla","preload_loaders":"Завантажую Forge / NeoForge / Fabric / Quilt","preload_news":"Завантажую новини Minecraft","preload_profiles":"Перевіряю профілі NullLauncher","ready":"Готово","cache_warning":"Частину мережевих даних завантажено з кешу.",
        "prompt_help":"Введіть значення · Enter — зберегти · Esc — скасувати","do_not_close":"Не закривайте термінал під час запису файлів.","news_unavailable":"Новини Minecraft зараз недоступні.","article":"Стаття","browser":"Браузер","color_error":"Використовуйте #RRGGBB або R,G,B, наприклад 98,244,255.",
        "settings_language":"Мова й оформлення","settings_network":"Мережа та введення","settings_storage":"Сховище та Java","settings_content":"Каталог і контент","settings_game":"Запуск гри","settings_maintenance":"Обслуговування",
    },
    "be": {
        "preparing":"Падрыхтоўка…","preload_local":"Чытаю лакальныя версіі","preload_vanilla":"Загружаю каталог Vanilla","preload_loaders":"Загружаю Forge / NeoForge / Fabric / Quilt","preload_news":"Загружаю навіны Minecraft","preload_profiles":"Правяраю профілі NullLauncher","ready":"Гатова","cache_warning":"Частка сеткавых даных загружана з кэша.",
        "prompt_help":"Увядзіце значэнне · Enter — захаваць · Esc — скасаваць","do_not_close":"Не закрывайце тэрмінал падчас запісу файлаў.","news_unavailable":"Навіны Minecraft зараз недаступныя.","article":"Артыкул","browser":"Браўзер","color_error":"Выкарыстоўвайце #RRGGBB або R,G,B, напрыклад 98,244,255.",
        "settings_language":"Мова і афармленне","settings_network":"Сетка і ўвод","settings_storage":"Сховішча і Java","settings_content":"Каталог і кантэнт","settings_game":"Запуск гульні","settings_maintenance":"Абслугоўванне",
    },
    "pl": {"preparing":"Przygotowanie…","preload_local":"Odczyt lokalnych wersji","preload_vanilla":"Ładowanie katalogu Vanilla","preload_loaders":"Ładowanie Forge / NeoForge / Fabric / Quilt","preload_news":"Ładowanie aktualności Minecraft","preload_profiles":"Sprawdzanie profili NullLauncher","ready":"Gotowe","cache_warning":"Część danych sieciowych wczytano z pamięci podręcznej.","prompt_help":"Wpisz wartość · Enter — zapisz · Esc — anuluj","do_not_close":"Nie zamykaj terminala podczas zapisywania plików.","news_unavailable":"Aktualności Minecraft są teraz niedostępne.","article":"Artykuł","browser":"Przeglądarka","color_error":"Użyj #RRGGBB albo R,G,B, np. 98,244,255."},
    "de": {"preparing":"Vorbereitung…","preload_local":"Lokale Versionen werden gelesen","preload_vanilla":"Vanilla-Katalog wird geladen","preload_loaders":"Forge / NeoForge / Fabric / Quilt werden geladen","preload_news":"Minecraft-News werden geladen","preload_profiles":"NullLauncher-Profile werden geprüft","ready":"Bereit","cache_warning":"Ein Teil der Netzwerkdaten wurde aus dem Cache geladen.","prompt_help":"Wert eingeben · Enter — speichern · Esc — abbrechen","do_not_close":"Terminal während des Schreibens nicht schließen.","news_unavailable":"Minecraft-News sind derzeit nicht verfügbar.","article":"Artikel","browser":"Browser","color_error":"Nutze #RRGGBB oder R,G,B, z. B. 98,244,255."},
    "fr": {"preparing":"Préparation…","preload_local":"Lecture des versions locales","preload_vanilla":"Chargement du catalogue Vanilla","preload_loaders":"Chargement de Forge / NeoForge / Fabric / Quilt","preload_news":"Chargement des actualités Minecraft","preload_profiles":"Vérification des profils NullLauncher","ready":"Prêt","cache_warning":"Certaines données réseau ont été chargées depuis le cache.","prompt_help":"Saisissez une valeur · Enter — enregistrer · Esc — annuler","do_not_close":"Ne fermez pas le terminal pendant l’écriture des fichiers.","news_unavailable":"Les actualités Minecraft sont indisponibles pour le moment.","article":"Article","browser":"Navigateur","color_error":"Utilisez #RRGGBB ou R,G,B, par ex. 98,244,255."},
    "es": {"preparing":"Preparando…","preload_local":"Leyendo versiones locales","preload_vanilla":"Cargando catálogo Vanilla","preload_loaders":"Cargando Forge / NeoForge / Fabric / Quilt","preload_news":"Cargando noticias de Minecraft","preload_profiles":"Comprobando perfiles de NullLauncher","ready":"Listo","cache_warning":"Parte de los datos de red se cargó desde la caché.","prompt_help":"Escribe un valor · Enter — guardar · Esc — cancelar","do_not_close":"No cierres el terminal mientras se escriben archivos.","news_unavailable":"Las noticias de Minecraft no están disponibles ahora.","article":"Artículo","browser":"Navegador","color_error":"Usa #RRGGBB o R,G,B, por ejemplo 98,244,255."},
    "pt-BR": {"preparing":"Preparando…","preload_local":"Lendo versões locais","preload_vanilla":"Carregando catálogo Vanilla","preload_loaders":"Carregando Forge / NeoForge / Fabric / Quilt","preload_news":"Carregando notícias do Minecraft","preload_profiles":"Verificando perfis do NullLauncher","ready":"Pronto","cache_warning":"Parte dos dados de rede foi carregada do cache.","prompt_help":"Digite um valor · Enter — salvar · Esc — cancelar","do_not_close":"Não feche o terminal enquanto os arquivos são gravados.","news_unavailable":"As notícias do Minecraft estão indisponíveis no momento.","article":"Artigo","browser":"Navegador","color_error":"Use #RRGGBB ou R,G,B, por exemplo 98,244,255."},
    "zh-CN": {"preparing":"正在准备…","preload_local":"正在读取本地版本","preload_vanilla":"正在加载 Vanilla 目录","preload_loaders":"正在加载 Forge / NeoForge / Fabric / Quilt","preload_news":"正在加载 Minecraft 新闻","preload_profiles":"正在检查 NullLauncher 配置","ready":"就绪","cache_warning":"部分网络数据已从缓存加载。","prompt_help":"输入值 · Enter — 保存 · Esc — 取消","do_not_close":"写入文件时请勿关闭终端。","news_unavailable":"Minecraft 新闻目前不可用。","article":"文章","browser":"浏览器","color_error":"请使用 #RRGGBB 或 R,G,B，例如 98,244,255。"},
    "ja": {"account":"アカウント","version":"バージョン","proxy":"プロキシ","none":"なし","not_selected":"未選択","yes":"はい","no":"いいえ","create_account":"オフラインアカウントを作成","select_launch":"起動用に選択","delete":"削除","create_proxy":"プロキシプロファイルを作成","direct":"直接接続","disable_proxy":"プロキシを無効化","type_proxy":"プロキシ種類","all":"すべて","catalog_summary":"{count} バージョン · ✓ インストール済み · ★ 選択中","download":"ダウンロード","version_settings":"バージョン設定","source":"ソース","open_browser":"ブラウザで記事を開く","back_news":"ニュースへ戻る","settings_paths":"パスと Java","settings_catalog":"カタログとコンテンツ","settings_interface":"インターフェース","settings_launch":"ゲーム起動","settings_actions":"操作","minecraft_dir":"Minecraft フォルダー","show_snapshots":"スナップショットを表示","show_old":"Alpha/Beta を表示","load_news":"Minecraft ニュースを読み込む","mouse_menu":"メニューでマウスを使用","repair":"起動前にファイルを検証","close_after":"ゲーム起動後にランチャーを閉じる","default_ram":"デフォルト RAM","java_manual":"Java パス","open_data":"NullLauncher フォルダーを開く","version_memory":"メモリ","version_window":"ウィンドウ","version_files":"ファイル","version_actions":"操作","ram_min":"最小 RAM","ram_max":"最大 RAM","custom_resolution":"カスタム解像度","window_size":"ウィンドウサイズ","separate_dir":"ゲームフォルダーを分離","language_title":"ランチャー言語","preparing":"準備中…","preload_local":"ローカルバージョンを確認中","preload_vanilla":"Vanilla カタログを読み込み中","preload_loaders":"Forge / NeoForge / Fabric / Quilt を読み込み中","preload_news":"Minecraft ニュースを読み込み中","preload_profiles":"NullLauncher プロファイルを確認中","ready":"準備完了","cache_warning":"一部のネットワークデータはキャッシュから読み込まれました。","prompt_help":"値を入力 · Enter — 保存 · Esc — キャンセル","do_not_close":"ファイル書き込み中はターミナルを閉じないでください。","news_unavailable":"Minecraft ニュースは現在利用できません。","article":"記事","browser":"ブラウザ","color_error":"#RRGGBB または R,G,B を使用してください。例: 98,244,255。"},
    "ko": {"account":"계정","version":"버전","proxy":"프록시","none":"없음","not_selected":"선택 안 됨","yes":"예","no":"아니요","create_account":"오프라인 계정 만들기","select_launch":"실행 계정으로 선택","delete":"삭제","create_proxy":"프록시 프로필 만들기","direct":"직접 연결","disable_proxy":"프록시 비활성화","type_proxy":"프록시 유형","all":"전체","catalog_summary":"{count}개 버전 · ✓ 설치됨 · ★ 선택됨","download":"다운로드","version_settings":"버전 설정","source":"소스","open_browser":"브라우저에서 기사 열기","back_news":"뉴스로 돌아가기","settings_paths":"경로 및 Java","settings_catalog":"카탈로그 및 콘텐츠","settings_interface":"인터페이스","settings_launch":"게임 실행","settings_actions":"작업","minecraft_dir":"Minecraft 폴더","show_snapshots":"스냅샷 표시","show_old":"Alpha/Beta 표시","load_news":"Minecraft 뉴스 불러오기","mouse_menu":"메뉴에서 마우스 사용","repair":"실행 전 파일 검사","close_after":"게임 실행 후 런처 닫기","default_ram":"기본 RAM","java_manual":"Java 경로","open_data":"NullLauncher 폴더 열기","version_memory":"메모리","version_window":"창","version_files":"파일","version_actions":"작업","ram_min":"최소 RAM","ram_max":"최대 RAM","custom_resolution":"사용자 지정 해상도","window_size":"창 크기","separate_dir":"별도 게임 폴더","language_title":"런처 언어","preparing":"준비 중…","preload_local":"로컬 버전 읽는 중","preload_vanilla":"Vanilla 카탈로그 불러오는 중","preload_loaders":"Forge / NeoForge / Fabric / Quilt 불러오는 중","preload_news":"Minecraft 뉴스 불러오는 중","preload_profiles":"NullLauncher 프로필 확인 중","ready":"준비 완료","cache_warning":"일부 네트워크 데이터를 캐시에서 불러왔습니다.","prompt_help":"값 입력 · Enter — 저장 · Esc — 취소","do_not_close":"파일을 쓰는 동안 터미널을 닫지 마세요.","news_unavailable":"Minecraft 뉴스를 현재 사용할 수 없습니다.","article":"기사","browser":"브라우저","color_error":"#RRGGBB 또는 R,G,B를 사용하세요. 예: 98,244,255."},
}
for _lang, _strings in _RUNTIME_TEXT.items():
    if _lang in I18N:
        I18N[_lang].update(_strings)

_OPS_TEXT = {
    "en": {"install_mll":"Installing minecraft-launcher-lib {version}…","install_mll_desc":"Automatic first-run setup; you do not need to download the library manually.","install_mll_fail":"Could not install minecraft-launcher-lib automatically. Check your internet connection and Python/pip access.","install_pillow":"Installing Pillow for Minecraft news images…","install_pillow_desc":"Automatic setup; you do not need to install Pillow manually.","install_pillow_fail":"Could not install Pillow automatically. Check your internet connection and Python/pip access.","fatal":"NullLauncher fatal error","log":"Log","python_required":"{app} requires Python {major}.{minor} or newer."},
    "ru": {"install_mll":"Устанавливаю minecraft-launcher-lib {version}…","install_mll_desc":"Автоматическая подготовка первого запуска; вручную скачивать библиотеку не нужно.","install_mll_fail":"Не удалось автоматически установить minecraft-launcher-lib. Проверьте интернет и доступ Python к pip.","install_pillow":"Устанавливаю Pillow для изображений новостей…","install_pillow_desc":"Автоматическая подготовка; вручную устанавливать Pillow не нужно.","install_pillow_fail":"Не удалось автоматически установить Pillow. Проверьте интернет и доступ Python к pip.","fatal":"Критическая ошибка NullLauncher","log":"Лог","python_required":"{app} требует Python {major}.{minor} или новее."},
    "uk": {"fatal":"Критична помилка NullLauncher","log":"Лог","python_required":"{app} потребує Python {major}.{minor} або новішої версії."},
    "be": {"fatal":"Крытычная памылка NullLauncher","log":"Лог","python_required":"{app} патрабуе Python {major}.{minor} або навей."},
}
for _lang, _strings in _OPS_TEXT.items():
    if _lang in I18N:
        I18N[_lang].update(_strings)

_UPDATE_TEXT = {
    "en": {"settings_updates":"Updates","auto_update":"Automatic updates","check_updates_now":"Check for updates now","checking_updates":"Checking GitHub for updates","update_downloading":"Downloading NullLauncher {version}","update_installing":"Installing NullLauncher {version}","update_restarting":"Update {version} is ready · restarting","update_none":"You already have the latest published NullLauncher release.","update_failed":"Update check failed: {error}"},
    "ru": {"settings_updates":"Обновления","auto_update":"Автоматические обновления","check_updates_now":"Проверить обновления сейчас","checking_updates":"Проверяю обновления на GitHub","update_downloading":"Скачиваю NullLauncher {version}","update_installing":"Устанавливаю NullLauncher {version}","update_restarting":"Обновление {version} готово · перезапуск","update_none":"У вас уже установлена последняя опубликованная версия NullLauncher.","update_failed":"Не удалось проверить обновления: {error}"},
    "uk": {"settings_updates":"Оновлення","auto_update":"Автоматичні оновлення","check_updates_now":"Перевірити оновлення зараз","checking_updates":"Перевіряю оновлення на GitHub","update_downloading":"Завантажую NullLauncher {version}","update_installing":"Встановлюю NullLauncher {version}","update_restarting":"Оновлення {version} готове · перезапуск","update_none":"У вас уже встановлено останній опублікований реліз NullLauncher.","update_failed":"Не вдалося перевірити оновлення: {error}"},
    "be": {"settings_updates":"Абнаўленні","auto_update":"Аўтаматычныя абнаўленні","check_updates_now":"Праверыць абнаўленні зараз","checking_updates":"Правяраю абнаўленні на GitHub","update_downloading":"Спампоўваю NullLauncher {version}","update_installing":"Усталёўваю NullLauncher {version}","update_restarting":"Абнаўленне {version} гатова · перазапуск","update_none":"У вас ужо апошні апублікаваны рэліз NullLauncher.","update_failed":"Не ўдалося праверыць абнаўленні: {error}"},
    "pl": {"settings_updates":"Aktualizacje","auto_update":"Automatyczne aktualizacje","check_updates_now":"Sprawdź aktualizacje teraz","checking_updates":"Sprawdzanie aktualizacji w GitHub","update_downloading":"Pobieranie NullLauncher {version}","update_installing":"Instalowanie NullLauncher {version}","update_restarting":"Aktualizacja {version} gotowa · ponowne uruchamianie","update_none":"Masz najnowsze opublikowane wydanie NullLauncher.","update_failed":"Nie udało się sprawdzić aktualizacji: {error}"},
    "de": {"settings_updates":"Updates","auto_update":"Automatische Updates","check_updates_now":"Jetzt nach Updates suchen","checking_updates":"GitHub wird nach Updates geprüft","update_downloading":"NullLauncher {version} wird heruntergeladen","update_installing":"NullLauncher {version} wird installiert","update_restarting":"Update {version} ist bereit · Neustart","update_none":"Du verwendest bereits die neueste veröffentlichte NullLauncher-Version.","update_failed":"Update-Prüfung fehlgeschlagen: {error}"},
    "fr": {"settings_updates":"Mises à jour","auto_update":"Mises à jour automatiques","check_updates_now":"Rechercher les mises à jour","checking_updates":"Recherche de mises à jour sur GitHub","update_downloading":"Téléchargement de NullLauncher {version}","update_installing":"Installation de NullLauncher {version}","update_restarting":"Mise à jour {version} prête · redémarrage","update_none":"Vous utilisez déjà la dernière version publiée de NullLauncher.","update_failed":"Échec de la vérification des mises à jour : {error}"},
    "es": {"settings_updates":"Actualizaciones","auto_update":"Actualizaciones automáticas","check_updates_now":"Buscar actualizaciones ahora","checking_updates":"Buscando actualizaciones en GitHub","update_downloading":"Descargando NullLauncher {version}","update_installing":"Instalando NullLauncher {version}","update_restarting":"Actualización {version} lista · reiniciando","update_none":"Ya tienes la última versión publicada de NullLauncher.","update_failed":"No se pudo comprobar las actualizaciones: {error}"},
    "pt-BR": {"settings_updates":"Atualizações","auto_update":"Atualizações automáticas","check_updates_now":"Verificar atualizações agora","checking_updates":"Verificando atualizações no GitHub","update_downloading":"Baixando NullLauncher {version}","update_installing":"Instalando NullLauncher {version}","update_restarting":"Atualização {version} pronta · reiniciando","update_none":"Você já tem a versão publicada mais recente do NullLauncher.","update_failed":"Falha ao verificar atualizações: {error}"},
    "zh-CN": {"settings_updates":"更新","auto_update":"自动更新","check_updates_now":"立即检查更新","checking_updates":"正在检查 GitHub 更新","update_downloading":"正在下载 NullLauncher {version}","update_installing":"正在安装 NullLauncher {version}","update_restarting":"更新 {version} 已就绪 · 正在重启","update_none":"你已经在使用最新发布的 NullLauncher。","update_failed":"检查更新失败：{error}"},
    "ja": {"settings_updates":"アップデート","auto_update":"自動アップデート","check_updates_now":"今すぐアップデートを確認","checking_updates":"GitHub のアップデートを確認中","update_downloading":"NullLauncher {version} をダウンロード中","update_installing":"NullLauncher {version} をインストール中","update_restarting":"アップデート {version} の準備完了 · 再起動中","update_none":"公開済みの最新 NullLauncher を使用しています。","update_failed":"アップデート確認に失敗しました: {error}"},
    "ko": {"settings_updates":"업데이트","auto_update":"자동 업데이트","check_updates_now":"지금 업데이트 확인","checking_updates":"GitHub 업데이트 확인 중","update_downloading":"NullLauncher {version} 다운로드 중","update_installing":"NullLauncher {version} 설치 중","update_restarting":"업데이트 {version} 준비 완료 · 재시작 중","update_none":"이미 최신 공개 NullLauncher 버전을 사용 중입니다.","update_failed":"업데이트 확인 실패: {error}"},
}
for _lang, _strings in _UPDATE_TEXT.items():
    if _lang in I18N:
        I18N[_lang].update(_strings)

_LAUNCH_TEXT = {
    "en": {"install_java_runtime":"Installing Java runtime","no_account_title":"No account","no_account_body":"Create an offline account in the Accounts section.","no_version_title":"No version selected","no_version_body":"Install or select a version in the Versions section.","version_missing_title":"Version not found","version_missing_body":"Files for {version} are missing. Select the version again.","verify_version":"Verifying {version}","empty_launch_command":"minecraft-launcher-lib returned an empty launch command","minecraft_running":"Minecraft {version} is running","player_label":"Player","proxy_label":"Proxy","log_label":"log","launcher_returns":"The launcher will return to the menu after the game closes.","game_failed_title":"Minecraft exited with an error","exit_code":"Exit code","last_log_lines":"Last log lines","empty_log":"log is empty","launch_error_title":"Launch error","see_launcher_log":"See null_launcher.log."},
    "ru": {"install_java_runtime":"Установка Java runtime","no_account_title":"Нет аккаунта","no_account_body":"Создайте offline-аккаунт в разделе «Аккаунты».","no_version_title":"Версия не выбрана","no_version_body":"Установите или выберите версию в разделе «Версии».","version_missing_title":"Версия не найдена","version_missing_body":"Файлы {version} отсутствуют. Выберите версию заново.","verify_version":"Проверка {version}","empty_launch_command":"minecraft-launcher-lib вернул пустую команду запуска","minecraft_running":"Minecraft {version} запущен","player_label":"Игрок","proxy_label":"Прокси","log_label":"лог","launcher_returns":"Лаунчер вернётся в меню после закрытия игры.","game_failed_title":"Minecraft завершился с ошибкой","exit_code":"Код выхода","last_log_lines":"Последние строки лога","empty_log":"лог пуст","launch_error_title":"Ошибка запуска","see_launcher_log":"См. null_launcher.log."},
    "uk": {"install_java_runtime":"Встановлення Java runtime","no_account_title":"Немає акаунта","no_account_body":"Створіть offline-акаунт у розділі «Акаунти».","no_version_title":"Версію не вибрано","no_version_body":"Встановіть або виберіть версію в розділі «Версії».","version_missing_title":"Версію не знайдено","version_missing_body":"Файли {version} відсутні. Виберіть версію знову.","verify_version":"Перевірка {version}","empty_launch_command":"minecraft-launcher-lib повернула порожню команду запуску","minecraft_running":"Minecraft {version} запущено","player_label":"Гравець","proxy_label":"Проксі","log_label":"лог","launcher_returns":"Лаунчер повернеться до меню після закриття гри.","game_failed_title":"Minecraft завершився з помилкою","exit_code":"Код виходу","last_log_lines":"Останні рядки логу","empty_log":"лог порожній","launch_error_title":"Помилка запуску","see_launcher_log":"Див. null_launcher.log."},
    "be": {"install_java_runtime":"Усталяванне Java runtime","no_account_title":"Няма акаўнта","no_account_body":"Стварыце offline-акаўнт у раздзеле «Акаўнты».","no_version_title":"Версія не выбрана","no_version_body":"Усталюйце або выберыце версію ў раздзеле «Версіі».","version_missing_title":"Версія не знойдзена","version_missing_body":"Файлы {version} адсутнічаюць. Выберыце версію нанова.","verify_version":"Праверка {version}","empty_launch_command":"minecraft-launcher-lib вярнула пустую каманду запуску","minecraft_running":"Minecraft {version} запушчаны","player_label":"Гулец","proxy_label":"Проксі","log_label":"лог","launcher_returns":"Лаунчар вернецца ў меню пасля закрыцця гульні.","game_failed_title":"Minecraft завяршыўся з памылкай","exit_code":"Код выхаду","last_log_lines":"Апошнія радкі лога","empty_log":"лог пусты","launch_error_title":"Памылка запуску","see_launcher_log":"Гл. null_launcher.log."},
    "pl": {"install_java_runtime":"Instalowanie środowiska Java","no_account_title":"Brak konta","no_account_body":"Utwórz konto offline w sekcji Konta.","no_version_title":"Nie wybrano wersji","no_version_body":"Zainstaluj lub wybierz wersję w sekcji Wersje.","version_missing_title":"Nie znaleziono wersji","version_missing_body":"Brakuje plików wersji {version}. Wybierz wersję ponownie.","verify_version":"Sprawdzanie {version}","empty_launch_command":"minecraft-launcher-lib zwróciła pustą komendę uruchomienia","minecraft_running":"Minecraft {version} jest uruchomiony","player_label":"Gracz","proxy_label":"Proxy","log_label":"log","launcher_returns":"Launcher wróci do menu po zamknięciu gry.","game_failed_title":"Minecraft zakończył się błędem","exit_code":"Kod wyjścia","last_log_lines":"Ostatnie linie logu","empty_log":"log jest pusty","launch_error_title":"Błąd uruchamiania","see_launcher_log":"Zobacz null_launcher.log."},
    "de": {"install_java_runtime":"Java-Laufzeit wird installiert","no_account_title":"Kein Konto","no_account_body":"Erstelle im Bereich Konten ein Offline-Konto.","no_version_title":"Keine Version ausgewählt","no_version_body":"Installiere oder wähle im Bereich Versionen eine Version aus.","version_missing_title":"Version nicht gefunden","version_missing_body":"Die Dateien für {version} fehlen. Wähle die Version erneut aus.","verify_version":"{version} wird überprüft","empty_launch_command":"minecraft-launcher-lib hat einen leeren Startbefehl zurückgegeben","minecraft_running":"Minecraft {version} läuft","player_label":"Spieler","proxy_label":"Proxy","log_label":"Log","launcher_returns":"Der Launcher kehrt nach dem Schließen des Spiels zum Menü zurück.","game_failed_title":"Minecraft wurde mit einem Fehler beendet","exit_code":"Exit-Code","last_log_lines":"Letzte Logzeilen","empty_log":"Log ist leer","launch_error_title":"Startfehler","see_launcher_log":"Siehe null_launcher.log."},
    "fr": {"install_java_runtime":"Installation de l’environnement Java","no_account_title":"Aucun compte","no_account_body":"Créez un compte hors ligne dans la section Comptes.","no_version_title":"Aucune version sélectionnée","no_version_body":"Installez ou sélectionnez une version dans la section Versions.","version_missing_title":"Version introuvable","version_missing_body":"Les fichiers de {version} sont absents. Sélectionnez de nouveau la version.","verify_version":"Vérification de {version}","empty_launch_command":"minecraft-launcher-lib a renvoyé une commande de lancement vide","minecraft_running":"Minecraft {version} est lancé","player_label":"Joueur","proxy_label":"Proxy","log_label":"journal","launcher_returns":"Le launcher reviendra au menu après la fermeture du jeu.","game_failed_title":"Minecraft s’est fermé avec une erreur","exit_code":"Code de sortie","last_log_lines":"Dernières lignes du journal","empty_log":"le journal est vide","launch_error_title":"Erreur de lancement","see_launcher_log":"Consultez null_launcher.log."},
    "es": {"install_java_runtime":"Instalando el entorno de Java","no_account_title":"No hay cuenta","no_account_body":"Crea una cuenta offline en la sección Cuentas.","no_version_title":"No hay versión seleccionada","no_version_body":"Instala o selecciona una versión en la sección Versiones.","version_missing_title":"Versión no encontrada","version_missing_body":"Faltan los archivos de {version}. Vuelve a seleccionar la versión.","verify_version":"Verificando {version}","empty_launch_command":"minecraft-launcher-lib devolvió un comando de inicio vacío","minecraft_running":"Minecraft {version} está en ejecución","player_label":"Jugador","proxy_label":"Proxy","log_label":"registro","launcher_returns":"El launcher volverá al menú cuando se cierre el juego.","game_failed_title":"Minecraft terminó con un error","exit_code":"Código de salida","last_log_lines":"Últimas líneas del registro","empty_log":"el registro está vacío","launch_error_title":"Error de inicio","see_launcher_log":"Consulta null_launcher.log."},
    "pt-BR": {"install_java_runtime":"Instalando o ambiente Java","no_account_title":"Nenhuma conta","no_account_body":"Crie uma conta offline na seção Contas.","no_version_title":"Nenhuma versão selecionada","no_version_body":"Instale ou selecione uma versão na seção Versões.","version_missing_title":"Versão não encontrada","version_missing_body":"Os arquivos de {version} estão ausentes. Selecione a versão novamente.","verify_version":"Verificando {version}","empty_launch_command":"minecraft-launcher-lib retornou um comando de inicialização vazio","minecraft_running":"Minecraft {version} está em execução","player_label":"Jogador","proxy_label":"Proxy","log_label":"log","launcher_returns":"O launcher voltará ao menu quando o jogo for fechado.","game_failed_title":"Minecraft foi encerrado com erro","exit_code":"Código de saída","last_log_lines":"Últimas linhas do log","empty_log":"o log está vazio","launch_error_title":"Erro ao iniciar","see_launcher_log":"Consulte null_launcher.log."},
    "zh-CN": {"install_java_runtime":"正在安装 Java 运行环境","no_account_title":"没有账户","no_account_body":"请在“账户”中创建离线账户。","no_version_title":"未选择版本","no_version_body":"请在“版本”中安装或选择一个版本。","version_missing_title":"未找到版本","version_missing_body":"缺少 {version} 的文件。请重新选择该版本。","verify_version":"正在验证 {version}","empty_launch_command":"minecraft-launcher-lib 返回了空的启动命令","minecraft_running":"Minecraft {version} 正在运行","player_label":"玩家","proxy_label":"代理","log_label":"日志","launcher_returns":"游戏关闭后，启动器将返回菜单。","game_failed_title":"Minecraft 因错误退出","exit_code":"退出代码","last_log_lines":"日志最后几行","empty_log":"日志为空","launch_error_title":"启动错误","see_launcher_log":"请查看 null_launcher.log。"},
    "ja": {"install_java_runtime":"Java ランタイムをインストール中","no_account_title":"アカウントがありません","no_account_body":"アカウント画面でオフラインアカウントを作成してください。","no_version_title":"バージョンが選択されていません","no_version_body":"バージョン画面でバージョンをインストールまたは選択してください。","version_missing_title":"バージョンが見つかりません","version_missing_body":"{version} のファイルがありません。バージョンをもう一度選択してください。","verify_version":"{version} を確認中","empty_launch_command":"minecraft-launcher-lib が空の起動コマンドを返しました","minecraft_running":"Minecraft {version} を実行中","player_label":"プレイヤー","proxy_label":"プロキシ","log_label":"ログ","launcher_returns":"ゲームを終了するとランチャーはメニューに戻ります。","game_failed_title":"Minecraft がエラーで終了しました","exit_code":"終了コード","last_log_lines":"ログの最後の行","empty_log":"ログは空です","launch_error_title":"起動エラー","see_launcher_log":"null_launcher.log を確認してください。"},
    "ko": {"install_java_runtime":"Java 런타임 설치 중","no_account_title":"계정 없음","no_account_body":"계정 메뉴에서 오프라인 계정을 만들어 주세요.","no_version_title":"버전이 선택되지 않음","no_version_body":"버전 메뉴에서 버전을 설치하거나 선택해 주세요.","version_missing_title":"버전을 찾을 수 없음","version_missing_body":"{version} 파일이 없습니다. 버전을 다시 선택해 주세요.","verify_version":"{version} 확인 중","empty_launch_command":"minecraft-launcher-lib가 빈 실행 명령을 반환했습니다","minecraft_running":"Minecraft {version} 실행 중","player_label":"플레이어","proxy_label":"프록시","log_label":"로그","launcher_returns":"게임을 종료하면 런처가 메뉴로 돌아갑니다.","game_failed_title":"Minecraft가 오류로 종료되었습니다","exit_code":"종료 코드","last_log_lines":"로그 마지막 줄","empty_log":"로그가 비어 있습니다","launch_error_title":"실행 오류","see_launcher_log":"null_launcher.log를 확인하세요."},
}
for _lang, _strings in _LAUNCH_TEXT.items():
    if _lang in I18N:
        I18N[_lang].update(_strings)


_NEWS_FILTER_TEXT = {
    "en": {"news_filter_color":"News image filter color"},
    "ru": {"news_filter_color":"Цвет фильтра изображений новостей"},
    "uk": {"news_filter_color":"Колір фільтра зображень новин"},
    "be": {"news_filter_color":"Колер фільтра выяў навін"},
    "pl": {"news_filter_color":"Kolor filtra obrazów aktualności"},
    "de": {"news_filter_color":"Filterfarbe für News-Bilder"},
    "fr": {"news_filter_color":"Couleur du filtre des images d’actualité"},
    "es": {"news_filter_color":"Color del filtro de imágenes de noticias"},
    "pt-BR": {"news_filter_color":"Cor do filtro das imagens de notícias"},
    "zh-CN": {"news_filter_color":"新闻图片滤镜颜色"},
    "ja": {"news_filter_color":"ニュース画像フィルター色"},
    "ko": {"news_filter_color":"뉴스 이미지 필터 색상"},
}
for _lang, _strings in _NEWS_FILTER_TEXT.items():
    if _lang in I18N:
        I18N[_lang].update(_strings)


_UX_TEXT = {
    "en": {
        "already_running":"NullLauncher is already running.",
        "first_run_title":"Welcome to NullLauncher",
        "first_run_language":"Choose launcher language",
        "first_run_minecraft_dir":"Minecraft folder",
        "first_run_ram_min":"Minimum RAM for Minecraft · MB",
        "first_run_ram_max":"Maximum RAM for Minecraft · MB",
        "first_run_account":"Offline account name (leave empty to skip)",
        "first_run_invalid_account":"Use 3–16 Latin letters, digits or _. You can also leave it empty and create an account later.",
        "first_run_done_title":"Setup complete",
        "first_run_done_body":"NullLauncher is ready. You can change these options later in Launcher settings.",
        "launching_title":"Launching Minecraft {version}",
        "launch_step":"Step {current}/{total}",
        "launch_stage_prepare":"Preparing launch",
        "launch_stage_verify":"Checking and downloading game files",
        "launch_stage_java":"Preparing Java runtime",
        "launch_stage_command":"Building launch command",
        "launch_stage_start":"Starting Minecraft",
        "launch_skipped":"File verification is disabled.",
        "launcher_crash_title":"NullLauncher encountered an error",
        "game_crash_title":"Minecraft closed unexpectedly",
        "open_log":"Open log",
        "open_log_folder":"Open log folder",
        "return_menu":"Return to menu",
        "crash_hint":"The launcher will stay open so you can inspect the log and return to the menu.",
        "clear_download_cache":"Clear download cache",
        "download_cache_cleared":"Download cache cleared.",
        "whats_new_title":"What's new in NullLauncher {version}",
        "whats_new_continue":"Continue",
        "no_release_notes":"This release has no changelog text.",
        "updated_from":"Updated from {old} to {new}",
    },
    "ru": {
        "already_running":"NullLauncher уже запущен.",
        "first_run_title":"Добро пожаловать в NullLauncher",
        "first_run_language":"Выберите язык лаунчера",
        "first_run_minecraft_dir":"Папка Minecraft",
        "first_run_ram_min":"Минимум RAM для Minecraft · МБ",
        "first_run_ram_max":"Максимум RAM для Minecraft · МБ",
        "first_run_account":"Имя offline-аккаунта (оставьте пустым, чтобы пропустить)",
        "first_run_invalid_account":"Используйте 3–16 латинских букв, цифр или _. Можно оставить поле пустым и создать аккаунт позже.",
        "first_run_done_title":"Настройка завершена",
        "first_run_done_body":"NullLauncher готов. Эти параметры можно изменить позже в настройках лаунчера.",
        "launching_title":"Запуск Minecraft {version}",
        "launch_step":"Шаг {current}/{total}",
        "launch_stage_prepare":"Подготовка запуска",
        "launch_stage_verify":"Проверка и загрузка файлов игры",
        "launch_stage_java":"Подготовка Java runtime",
        "launch_stage_command":"Создание команды запуска",
        "launch_stage_start":"Запуск Minecraft",
        "launch_skipped":"Проверка файлов отключена.",
        "launcher_crash_title":"В NullLauncher произошла ошибка",
        "game_crash_title":"Minecraft неожиданно завершился",
        "open_log":"Открыть лог",
        "open_log_folder":"Открыть папку лога",
        "return_menu":"Вернуться в меню",
        "crash_hint":"Лаунчер останется открытым: можно посмотреть лог и вернуться в меню.",
        "clear_download_cache":"Очистить кэш загрузок",
        "download_cache_cleared":"Кэш загрузок очищен.",
        "whats_new_title":"Что нового в NullLauncher {version}",
        "whats_new_continue":"Продолжить",
        "no_release_notes":"Для этого релиза не указано описание изменений.",
        "updated_from":"Обновлено с {old} до {new}",
    },
    "uk": {
        "already_running":"NullLauncher уже запущено.",
        "first_run_title":"Ласкаво просимо до NullLauncher",
        "first_run_language":"Оберіть мову лаунчера",
        "first_run_minecraft_dir":"Папка Minecraft",
        "first_run_ram_min":"Мінімум RAM для Minecraft · МБ",
        "first_run_ram_max":"Максимум RAM для Minecraft · МБ",
        "first_run_account":"Ім'я offline-акаунта (залиште порожнім, щоб пропустити)",
        "first_run_invalid_account":"Використовуйте 3–16 латинських літер, цифр або _. Поле можна залишити порожнім.",
        "first_run_done_title":"Налаштування завершено",
        "first_run_done_body":"NullLauncher готовий. Параметри можна змінити пізніше в налаштуваннях.",
        "launching_title":"Запуск Minecraft {version}",
        "launch_step":"Крок {current}/{total}",
        "launch_stage_prepare":"Підготовка запуску",
        "launch_stage_verify":"Перевірка та завантаження файлів гри",
        "launch_stage_java":"Підготовка Java runtime",
        "launch_stage_command":"Створення команди запуску",
        "launch_stage_start":"Запуск Minecraft",
        "launch_skipped":"Перевірку файлів вимкнено.",
        "launcher_crash_title":"У NullLauncher сталася помилка",
        "game_crash_title":"Minecraft несподівано завершився",
        "open_log":"Відкрити лог",
        "open_log_folder":"Відкрити папку лога",
        "return_menu":"Повернутися до меню",
        "crash_hint":"Лаунчер залишиться відкритим, щоб ви могли переглянути лог і повернутися до меню.",
        "clear_download_cache":"Очистити кеш завантажень",
        "download_cache_cleared":"Кеш завантажень очищено.",
        "whats_new_title":"Що нового в NullLauncher {version}",
        "whats_new_continue":"Продовжити",
        "no_release_notes":"Для цього релізу немає опису змін.",
        "updated_from":"Оновлено з {old} до {new}",
    },
    "be": {
        "already_running":"NullLauncher ужо запушчаны.",
        "first_run_title":"Вітаем у NullLauncher",
        "first_run_language":"Выберыце мову лаунчара",
        "first_run_minecraft_dir":"Папка Minecraft",
        "first_run_ram_min":"Мінімум RAM для Minecraft · МБ",
        "first_run_ram_max":"Максімум RAM для Minecraft · МБ",
        "first_run_account":"Імя offline-акаўнта (пакіньце пустым, каб прапусціць)",
        "first_run_invalid_account":"Выкарыстоўвайце 3–16 лацінскіх літар, лічбаў або _. Поле можна пакінуць пустым.",
        "first_run_done_title":"Наладжванне завершана",
        "first_run_done_body":"NullLauncher гатовы. Параметры можна змяніць пазней у наладах.",
        "launching_title":"Запуск Minecraft {version}",
        "launch_step":"Крок {current}/{total}",
        "launch_stage_prepare":"Падрыхтоўка запуску",
        "launch_stage_verify":"Праверка і загрузка файлаў гульні",
        "launch_stage_java":"Падрыхтоўка Java runtime",
        "launch_stage_command":"Стварэнне каманды запуску",
        "launch_stage_start":"Запуск Minecraft",
        "launch_skipped":"Праверка файлаў адключана.",
        "launcher_crash_title":"У NullLauncher адбылася памылка",
        "game_crash_title":"Minecraft нечакана завяршыўся",
        "open_log":"Адкрыць лог",
        "open_log_folder":"Адкрыць папку лога",
        "return_menu":"Вярнуцца ў меню",
        "crash_hint":"Лаунчар застанецца адкрытым, каб можна было прагледзець лог і вярнуцца ў меню.",
        "clear_download_cache":"Ачысціць кэш загрузак",
        "download_cache_cleared":"Кэш загрузак ачышчаны.",
        "whats_new_title":"Што новага ў NullLauncher {version}",
        "whats_new_continue":"Працягнуць",
        "no_release_notes":"Для гэтага рэлізу няма апісання змен.",
        "updated_from":"Абноўлена з {old} да {new}",
    },
    "pl": {
        "already_running":"NullLauncher jest już uruchomiony.",
        "first_run_title":"Witamy w NullLauncher",
        "first_run_language":"Wybierz język launchera",
        "first_run_minecraft_dir":"Folder Minecraft",
        "first_run_ram_min":"Minimalna RAM dla Minecraft · MB",
        "first_run_ram_max":"Maksymalna RAM dla Minecraft · MB",
        "first_run_account":"Nazwa konta offline (pozostaw puste, aby pominąć)",
        "first_run_invalid_account":"Użyj 3–16 liter łacińskich, cyfr lub _. Możesz też pozostawić pole puste.",
        "first_run_done_title":"Konfiguracja zakończona",
        "first_run_done_body":"NullLauncher jest gotowy. Ustawienia można później zmienić.",
        "launching_title":"Uruchamianie Minecraft {version}",
        "launch_step":"Krok {current}/{total}",
        "launch_stage_prepare":"Przygotowanie uruchomienia",
        "launch_stage_verify":"Sprawdzanie i pobieranie plików gry",
        "launch_stage_java":"Przygotowanie środowiska Java",
        "launch_stage_command":"Tworzenie polecenia uruchomienia",
        "launch_stage_start":"Uruchamianie Minecraft",
        "launch_skipped":"Weryfikacja plików jest wyłączona.",
        "launcher_crash_title":"W NullLauncher wystąpił błąd",
        "game_crash_title":"Minecraft nieoczekiwanie się zakończył",
        "open_log":"Otwórz log",
        "open_log_folder":"Otwórz folder logu",
        "return_menu":"Wróć do menu",
        "crash_hint":"Launcher pozostanie otwarty, aby można było sprawdzić log i wrócić do menu.",
        "clear_download_cache":"Wyczyść pamięć pobierania",
        "download_cache_cleared":"Pamięć pobierania została wyczyszczona.",
        "whats_new_title":"Co nowego w NullLauncher {version}",
        "whats_new_continue":"Kontynuuj",
        "no_release_notes":"Ten release nie zawiera opisu zmian.",
        "updated_from":"Zaktualizowano z {old} do {new}",
    },
    "de": {
        "already_running":"NullLauncher läuft bereits.",
        "first_run_title":"Willkommen bei NullLauncher",
        "first_run_language":"Launcher-Sprache auswählen",
        "first_run_minecraft_dir":"Minecraft-Ordner",
        "first_run_ram_min":"Minimaler RAM für Minecraft · MB",
        "first_run_ram_max":"Maximaler RAM für Minecraft · MB",
        "first_run_account":"Offline-Kontoname (leer lassen zum Überspringen)",
        "first_run_invalid_account":"Verwende 3–16 lateinische Buchstaben, Ziffern oder _. Das Feld kann leer bleiben.",
        "first_run_done_title":"Einrichtung abgeschlossen",
        "first_run_done_body":"NullLauncher ist bereit. Diese Optionen können später geändert werden.",
        "launching_title":"Minecraft {version} wird gestartet",
        "launch_step":"Schritt {current}/{total}",
        "launch_stage_prepare":"Start wird vorbereitet",
        "launch_stage_verify":"Spieldateien werden geprüft und geladen",
        "launch_stage_java":"Java-Laufzeit wird vorbereitet",
        "launch_stage_command":"Startbefehl wird erstellt",
        "launch_stage_start":"Minecraft wird gestartet",
        "launch_skipped":"Dateiprüfung ist deaktiviert.",
        "launcher_crash_title":"NullLauncher hat einen Fehler festgestellt",
        "game_crash_title":"Minecraft wurde unerwartet beendet",
        "open_log":"Log öffnen",
        "open_log_folder":"Log-Ordner öffnen",
        "return_menu":"Zum Menü zurückkehren",
        "crash_hint":"Der Launcher bleibt geöffnet, damit du das Log prüfen und zum Menü zurückkehren kannst.",
        "clear_download_cache":"Download-Cache leeren",
        "download_cache_cleared":"Download-Cache wurde geleert.",
        "whats_new_title":"Neu in NullLauncher {version}",
        "whats_new_continue":"Weiter",
        "no_release_notes":"Für dieses Release gibt es keinen Änderungstext.",
        "updated_from":"Aktualisiert von {old} auf {new}",
    },
    "fr": {
        "already_running":"NullLauncher est déjà lancé.",
        "first_run_title":"Bienvenue dans NullLauncher",
        "first_run_language":"Choisissez la langue du launcher",
        "first_run_minecraft_dir":"Dossier Minecraft",
        "first_run_ram_min":"RAM minimale pour Minecraft · Mo",
        "first_run_ram_max":"RAM maximale pour Minecraft · Mo",
        "first_run_account":"Nom du compte hors ligne (laissez vide pour ignorer)",
        "first_run_invalid_account":"Utilisez 3 à 16 lettres latines, chiffres ou _. Vous pouvez aussi laisser le champ vide.",
        "first_run_done_title":"Configuration terminée",
        "first_run_done_body":"NullLauncher est prêt. Ces options pourront être modifiées plus tard.",
        "launching_title":"Lancement de Minecraft {version}",
        "launch_step":"Étape {current}/{total}",
        "launch_stage_prepare":"Préparation du lancement",
        "launch_stage_verify":"Vérification et téléchargement des fichiers du jeu",
        "launch_stage_java":"Préparation de l’environnement Java",
        "launch_stage_command":"Création de la commande de lancement",
        "launch_stage_start":"Démarrage de Minecraft",
        "launch_skipped":"La vérification des fichiers est désactivée.",
        "launcher_crash_title":"NullLauncher a rencontré une erreur",
        "game_crash_title":"Minecraft s’est fermé de façon inattendue",
        "open_log":"Ouvrir le journal",
        "open_log_folder":"Ouvrir le dossier du journal",
        "return_menu":"Retour au menu",
        "crash_hint":"Le launcher reste ouvert pour vous permettre de consulter le journal et de revenir au menu.",
        "clear_download_cache":"Vider le cache de téléchargement",
        "download_cache_cleared":"Cache de téléchargement vidé.",
        "whats_new_title":"Nouveautés de NullLauncher {version}",
        "whats_new_continue":"Continuer",
        "no_release_notes":"Cette version ne contient pas de notes de changement.",
        "updated_from":"Mis à jour de {old} vers {new}",
    },
    "es": {
        "already_running":"NullLauncher ya está en ejecución.",
        "first_run_title":"Bienvenido a NullLauncher",
        "first_run_language":"Elige el idioma del launcher",
        "first_run_minecraft_dir":"Carpeta de Minecraft",
        "first_run_ram_min":"RAM mínima para Minecraft · MB",
        "first_run_ram_max":"RAM máxima para Minecraft · MB",
        "first_run_account":"Nombre de cuenta offline (déjalo vacío para omitir)",
        "first_run_invalid_account":"Usa 3–16 letras latinas, números o _. También puedes dejarlo vacío.",
        "first_run_done_title":"Configuración completada",
        "first_run_done_body":"NullLauncher está listo. Puedes cambiar estas opciones más tarde.",
        "launching_title":"Iniciando Minecraft {version}",
        "launch_step":"Paso {current}/{total}",
        "launch_stage_prepare":"Preparando el inicio",
        "launch_stage_verify":"Comprobando y descargando archivos del juego",
        "launch_stage_java":"Preparando el entorno Java",
        "launch_stage_command":"Creando el comando de inicio",
        "launch_stage_start":"Iniciando Minecraft",
        "launch_skipped":"La verificación de archivos está desactivada.",
        "launcher_crash_title":"NullLauncher encontró un error",
        "game_crash_title":"Minecraft se cerró inesperadamente",
        "open_log":"Abrir registro",
        "open_log_folder":"Abrir carpeta del registro",
        "return_menu":"Volver al menú",
        "crash_hint":"El launcher permanecerá abierto para que puedas revisar el registro y volver al menú.",
        "clear_download_cache":"Vaciar caché de descargas",
        "download_cache_cleared":"Caché de descargas vaciada.",
        "whats_new_title":"Novedades de NullLauncher {version}",
        "whats_new_continue":"Continuar",
        "no_release_notes":"Esta versión no tiene texto de cambios.",
        "updated_from":"Actualizado de {old} a {new}",
    },
    "pt-BR": {
        "already_running":"NullLauncher já está em execução.",
        "first_run_title":"Bem-vindo ao NullLauncher",
        "first_run_language":"Escolha o idioma do launcher",
        "first_run_minecraft_dir":"Pasta do Minecraft",
        "first_run_ram_min":"RAM mínima para Minecraft · MB",
        "first_run_ram_max":"RAM máxima para Minecraft · MB",
        "first_run_account":"Nome da conta offline (deixe vazio para pular)",
        "first_run_invalid_account":"Use 3–16 letras latinas, números ou _. Você também pode deixar vazio.",
        "first_run_done_title":"Configuração concluída",
        "first_run_done_body":"NullLauncher está pronto. Essas opções podem ser alteradas depois.",
        "launching_title":"Iniciando Minecraft {version}",
        "launch_step":"Etapa {current}/{total}",
        "launch_stage_prepare":"Preparando inicialização",
        "launch_stage_verify":"Verificando e baixando arquivos do jogo",
        "launch_stage_java":"Preparando ambiente Java",
        "launch_stage_command":"Criando comando de inicialização",
        "launch_stage_start":"Iniciando Minecraft",
        "launch_skipped":"A verificação de arquivos está desativada.",
        "launcher_crash_title":"NullLauncher encontrou um erro",
        "game_crash_title":"Minecraft foi encerrado inesperadamente",
        "open_log":"Abrir log",
        "open_log_folder":"Abrir pasta do log",
        "return_menu":"Voltar ao menu",
        "crash_hint":"O launcher permanecerá aberto para você consultar o log e voltar ao menu.",
        "clear_download_cache":"Limpar cache de downloads",
        "download_cache_cleared":"Cache de downloads limpo.",
        "whats_new_title":"Novidades do NullLauncher {version}",
        "whats_new_continue":"Continuar",
        "no_release_notes":"Esta versão não possui texto de alterações.",
        "updated_from":"Atualizado de {old} para {new}",
    },
    "zh-CN": {
        "already_running":"NullLauncher 已在运行。",
        "first_run_title":"欢迎使用 NullLauncher",
        "first_run_language":"选择启动器语言",
        "first_run_minecraft_dir":"Minecraft 文件夹",
        "first_run_ram_min":"Minecraft 最小内存 · MB",
        "first_run_ram_max":"Minecraft 最大内存 · MB",
        "first_run_account":"离线账户名（留空可跳过）",
        "first_run_invalid_account":"请使用 3–16 个拉丁字母、数字或 _，也可以留空稍后创建。",
        "first_run_done_title":"设置完成",
        "first_run_done_body":"NullLauncher 已准备就绪。稍后可在设置中修改这些选项。",
        "launching_title":"正在启动 Minecraft {version}",
        "launch_step":"步骤 {current}/{total}",
        "launch_stage_prepare":"准备启动",
        "launch_stage_verify":"检查并下载游戏文件",
        "launch_stage_java":"准备 Java 运行环境",
        "launch_stage_command":"生成启动命令",
        "launch_stage_start":"启动 Minecraft",
        "launch_skipped":"文件验证已关闭。",
        "launcher_crash_title":"NullLauncher 遇到错误",
        "game_crash_title":"Minecraft 意外退出",
        "open_log":"打开日志",
        "open_log_folder":"打开日志文件夹",
        "return_menu":"返回菜单",
        "crash_hint":"启动器会保持打开，方便你查看日志并返回菜单。",
        "clear_download_cache":"清除下载缓存",
        "download_cache_cleared":"下载缓存已清除。",
        "whats_new_title":"NullLauncher {version} 更新内容",
        "whats_new_continue":"继续",
        "no_release_notes":"此版本没有更新说明。",
        "updated_from":"已从 {old} 更新到 {new}",
    },
    "ja": {
        "already_running":"NullLauncher はすでに起動しています。",
        "first_run_title":"NullLauncher へようこそ",
        "first_run_language":"ランチャーの言語を選択",
        "first_run_minecraft_dir":"Minecraft フォルダー",
        "first_run_ram_min":"Minecraft 最小 RAM · MB",
        "first_run_ram_max":"Minecraft 最大 RAM · MB",
        "first_run_account":"オフラインアカウント名（空欄でスキップ）",
        "first_run_invalid_account":"3～16文字の英字、数字、_ を使用してください。空欄のまま後で作成することもできます。",
        "first_run_done_title":"セットアップ完了",
        "first_run_done_body":"NullLauncher の準備ができました。これらの設定は後で変更できます。",
        "launching_title":"Minecraft {version} を起動中",
        "launch_step":"ステップ {current}/{total}",
        "launch_stage_prepare":"起動準備",
        "launch_stage_verify":"ゲームファイルを確認・ダウンロード中",
        "launch_stage_java":"Java ランタイムを準備中",
        "launch_stage_command":"起動コマンドを作成中",
        "launch_stage_start":"Minecraft を起動中",
        "launch_skipped":"ファイル確認は無効です。",
        "launcher_crash_title":"NullLauncher でエラーが発生しました",
        "game_crash_title":"Minecraft が予期せず終了しました",
        "open_log":"ログを開く",
        "open_log_folder":"ログフォルダーを開く",
        "return_menu":"メニューに戻る",
        "crash_hint":"ログを確認してメニューへ戻れるよう、ランチャーは開いたままになります。",
        "clear_download_cache":"ダウンロードキャッシュを消去",
        "download_cache_cleared":"ダウンロードキャッシュを消去しました。",
        "whats_new_title":"NullLauncher {version} の新機能",
        "whats_new_continue":"続行",
        "no_release_notes":"このリリースには変更内容がありません。",
        "updated_from":"{old} から {new} に更新しました",
    },
    "ko": {
        "already_running":"NullLauncher가 이미 실행 중입니다.",
        "first_run_title":"NullLauncher에 오신 것을 환영합니다",
        "first_run_language":"런처 언어 선택",
        "first_run_minecraft_dir":"Minecraft 폴더",
        "first_run_ram_min":"Minecraft 최소 RAM · MB",
        "first_run_ram_max":"Minecraft 최대 RAM · MB",
        "first_run_account":"오프라인 계정 이름(비워 두면 건너뜀)",
        "first_run_invalid_account":"3~16자의 영문자, 숫자 또는 _를 사용하세요. 비워 두고 나중에 만들 수도 있습니다.",
        "first_run_done_title":"설정 완료",
        "first_run_done_body":"NullLauncher가 준비되었습니다. 이 설정은 나중에 변경할 수 있습니다.",
        "launching_title":"Minecraft {version} 실행 중",
        "launch_step":"단계 {current}/{total}",
        "launch_stage_prepare":"실행 준비",
        "launch_stage_verify":"게임 파일 확인 및 다운로드",
        "launch_stage_java":"Java 런타임 준비",
        "launch_stage_command":"실행 명령 생성",
        "launch_stage_start":"Minecraft 시작",
        "launch_skipped":"파일 확인이 비활성화되어 있습니다.",
        "launcher_crash_title":"NullLauncher에서 오류가 발생했습니다",
        "game_crash_title":"Minecraft가 예기치 않게 종료되었습니다",
        "open_log":"로그 열기",
        "open_log_folder":"로그 폴더 열기",
        "return_menu":"메뉴로 돌아가기",
        "crash_hint":"로그를 확인하고 메뉴로 돌아갈 수 있도록 런처가 열린 상태로 유지됩니다.",
        "clear_download_cache":"다운로드 캐시 지우기",
        "download_cache_cleared":"다운로드 캐시를 지웠습니다.",
        "whats_new_title":"NullLauncher {version} 변경 사항",
        "whats_new_continue":"계속",
        "no_release_notes":"이 릴리스에는 변경 내용이 없습니다.",
        "updated_from":"{old}에서 {new}(으)로 업데이트됨",
    },
}
for _lang, _strings in _UX_TEXT.items():
    if _lang in I18N:
        I18N[_lang].update(_strings)

CURRENT_LANGUAGE = "en"
PRIMARY_COLOR = "\x1b[38;2;98;244;255m"
SUBTITLE_COLOR = "\x1b[38;2;154;164;173m"
STATUS_COLOR = "\x1b[38;2;115;125;134m"
THEME_PRIMARY_RGB = (98, 244, 255)
NEWS_FILTER_RGB = (98, 244, 255)

def tr(key: str, **values: Any) -> str:
    table = I18N.get(CURRENT_LANGUAGE, I18N["en"])
    text = table.get(key, I18N["en"].get(key, key))
    try:
        return text.format(**values)
    except Exception:
        return text

def _parse_rgb(value: Any, default: str) -> tuple[str, tuple[int, int, int]]:
    raw = str(value or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", raw)
    if m:
        h = m.group(1).upper()
        rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        return "#" + h, rgb                              
    m = re.fullmatch(r"\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*", raw)
    if m:
        vals = tuple(max(0, min(255, int(x))) for x in m.groups())
        return "#%02X%02X%02X" % vals, vals                              
    if raw != default:
        return _parse_rgb(default, default)
    return "#FFFFFF", (255, 255, 255)

def normalize_user_color(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", raw)
    if m:
        return "#" + m.group(1).upper()
    m = re.fullmatch(r"\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*", raw)
    if m:
        vals = tuple(int(x) for x in m.groups())
        if all(0 <= x <= 255 for x in vals):
            return "#%02X%02X%02X" % vals
    return None

def apply_runtime_preferences(settings: dict[str, Any]) -> None:
    global CURRENT_LANGUAGE, PRIMARY_COLOR, SUBTITLE_COLOR, STATUS_COLOR, THEME_PRIMARY_RGB, NEWS_FILTER_RGB
    language = str(settings.get("language") or "en")
    CURRENT_LANGUAGE = language if language in LANGUAGES else "en"
    p, prgb = _parse_rgb(settings.get("theme_primary"), DEFAULT_THEME["theme_primary"])
    s, srgb = _parse_rgb(settings.get("theme_subtitle"), DEFAULT_THEME["theme_subtitle"])
    st, strgb = _parse_rgb(settings.get("theme_status"), DEFAULT_THEME["theme_status"])
    nf, nfrgb = _parse_rgb(settings.get("news_filter_color"), DEFAULT_THEME["news_filter_color"])
    settings["theme_primary"], settings["theme_subtitle"], settings["theme_status"], settings["news_filter_color"] = p, s, st, nf
    PRIMARY_COLOR = f"\x1b[38;2;{prgb[0]};{prgb[1]};{prgb[2]}m"
    SUBTITLE_COLOR = f"\x1b[38;2;{srgb[0]};{srgb[1]};{srgb[2]}m"
    STATUS_COLOR = f"\x1b[38;2;{strgb[0]};{strgb[1]};{strgb[2]}m"
    THEME_PRIMARY_RGB = prgb
    NEWS_FILTER_RGB = nfrgb

def section_label(text: str) -> str:
    return f"──── {text} ────"
