#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Скрипт для создания .env файла с токеном бота"""

import os

BOT_TOKEN = "8554165803:AAGc4bk3_WC0QQSgPjwn7JE9I4NLyEOUDr"

def create_env_file():
    """Создает файл .env с токеном бота"""
    env_content = f"BOT_TOKEN={BOT_TOKEN}\n"
    
    try:
        with open('.env', 'w', encoding='utf-8') as f:
            f.write(env_content)
        print("✅ Файл .env успешно создан!")
        print("📝 Токен бота добавлен в файл .env")
        return True
    except Exception as e:
        print(f"❌ Ошибка при создании файла .env: {e}")
        return False

if __name__ == "__main__":
    if os.path.exists('.env'):
        response = input("Файл .env уже существует. Перезаписать? (y/n): ")
        if response.lower() != 'y':
            print("Отменено.")
            exit(0)
    
    create_env_file()

