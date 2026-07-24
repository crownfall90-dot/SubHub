# OTA — обновления SubHub

## Два пути обновления

| Условие | Метод | Функция |
|---------|-------|---------|
| Есть `.git/` + git в PATH | `git fetch` + `git merge --ff-only` | `_do_git_update()` |
| ZIP без git / git недоступен | HTTP raw GitHub | `_http_do_update()` |

Обе ветки в `subhub/menu.py`.

## Список OTA-файлов — `_UPDATE_FILES`

**Новый deployable файл → добавь в список.**

См. актуальный список в `subhub/menu.py` → `_UPDATE_FILES` (пути вида `subhub/...`, `menu.bat`, `requirements.txt`, examples).

### НЕ в OTA

| Путь | Как обновить |
|------|--------------|
| `config.yaml`, `secrets.yaml` | Локально |
| `data/`, профили Chrome | Runtime |
| `.cursor/`, `vendor/` | git |

## После обновления

Перезапуск — exit code **42** (`menu.bat` restart loop).

## Чеклист

```
- [ ] Файл в _UPDATE_FILES (если нужен ZIP/OTA)
- [ ] Новая зависимость → requirements.txt
- [ ] Новый config-ключ → config.yaml.example + migrate
- [ ] После merge в master — «Проверить обновления» в консоли
```
