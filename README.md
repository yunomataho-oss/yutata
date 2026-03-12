# 図面番号検索アプリ — Drawing Number Search

図面ファイル（PDF / DXF / DWG / SLDDRW）の中のテキストを自動解析し、**図面番号でファイルを高速検索**するデスクトップアプリケーションです。

---

## 機能概要

| 機能 | 内容 |
|------|------|
| **対応形式** | PDF / DXF / DWG / SLDDRW (SolidWorks) |
| **テキスト抽出** | ファイル内の文字列を自動解析 |
| **図面番号検出** | タイトルブロック・注記から自動抽出 |
| **全文検索** | キーワード・図面番号・ファイル名で検索 |
| **正規表現検索** | `DRW-\d+` などのパターン検索対応 |
| **インデックス永続化** | JSON形式で保存、次回起動時も即検索 |
| **GUI + CLI** | Tkinter GUI + コマンドライン両対応 |

---

## ファイル構成

```
drawing_number_search/
├── src/
│   ├── extractors/
│   │   ├── base_extractor.py     # 基底クラス
│   │   ├── pdf_extractor.py      # PDF → PyMuPDF
│   │   ├── dxf_extractor.py      # DXF → ezdxf
│   │   ├── dwg_extractor.py      # DWG → ODA変換 + ezdxf
│   │   └── slddrw_extractor.py   # SLDDRW → SW COM API / バイナリスキャン
│   ├── search/
│   │   └── search_engine.py      # インデックス・検索エンジン
│   ├── gui/
│   │   └── app.py                # Tkinter GUI
│   ├── utils/
│   │   └── helpers.py            # ログ・設定ユーティリティ
│   └── cli.py                    # CLI エントリポイント
├── tests/
│   ├── test_all.py               # ユニットテスト (pytest)
│   └── create_samples.py         # サンプルファイル生成
├── sample_files/                 # テスト用サンプル図面
├── requirements.txt
├── setup.sh   (Linux/macOS)
└── setup.bat  (Windows)
```

---

## セットアップ

### 必要環境
- **Python 3.9 以上**
- `pip` (Python パッケージマネージャ)
- **tkinter** (GUI用 — 通常 Python に同梱)

### インストール

#### Linux / macOS
```bash
chmod +x setup.sh
./setup.sh
```

#### Windows
```bat
setup.bat
```

#### 手動インストール
```bash
pip install PyMuPDF ezdxf
```

---

## 使い方

### GUI (推奨)

```bash
# Linux / macOS
./run_gui.sh

# Windows
run_gui.bat

# またはコマンドで
python -m src gui
```

**操作手順:**
1. **「📂 フォルダ登録」** ボタン → 図面ファイルが入ったフォルダを選択
2. 検索バーに**図面番号**またはキーワードを入力
3. **「検索」** ボタン または `Enter` キー
4. 結果をダブルクリック → ファイルを直接開く

![GUI Screenshot](docs/screenshot.png)

### CLI

```bash
# フォルダをインデックス登録
python -m src index /path/to/drawings

# サブフォルダなし
python -m src index /path/to/drawings --no-recursive

# 単一ファイルを登録
python -m src index /path/to/drawing.pdf

# 図面番号を検索
python -m src search "DRW-001"

# 正規表現検索
python -m src search "DRW-\d+" --regex

# 図面番号フィールドのみ検索
python -m src search "AB-1234" --match-type drawing_number

# 全文検索
python -m src search "設計変更" --match-type full_text

# JSON形式で出力
python -m src search "DRW-001" --json

# インデックス済みファイル一覧
python -m src list
```

---

## ファイル形式別の解析方法

### PDF
- **PyMuPDF (fitz)** でテキストレイヤーを直接抽出
- アノテーション・注釈も解析対象
- テキストブロック単位で詳細抽出

### DXF
- **ezdxf** ライブラリで完全解析
- 対象エンティティ: `TEXT`, `MTEXT`, `ATTRIB`, `ATTDEF`, `INSERT`, `DIMENSION`
- モデルスペース・ペーパースペース・ブロック定義すべてを走査

### DWG
| 条件 | 解析方法 |
|------|----------|
| **ODA File Converter あり** | DWG → DXF 変換後 ezdxf で解析（高精度） |
| **ODA File Converter なし** | バイナリスキャンで文字列抽出（簡易） |

> **ODA File Converter** (無料) ダウンロード:  
> https://www.opendesign.com/guestfiles/oda_file_converter

### SLDDRW (SolidWorks)
| 条件 | 解析方法 |
|------|----------|
| **Windows + SolidWorks インストール済み** | COM API で全注記・カスタムプロパティ取得（最高精度） |
| **それ以外** | ZIP/XML解析 + バイナリスキャン |

---

## 図面番号の検出パターン

デフォルトで以下のパターンを自動認識します：

| パターン例 | 説明 |
|-----------|------|
| `DRW-12345` | DRW/DWG/DRAW プレフィックス |
| `図番: DRW-001` | 日本語ラベル付き |
| `AB-1234`, `XYZ-5678` | 英字プレフィックス + 数字 |
| `12345-REV-A` | 数字ベース図面番号 |

---

## テスト実行

```bash
pytest tests/ -v
```

---

## 設定ファイル

設定は `~/.drawing_search/config.json` に自動保存されます。

```json
{
  "index_path": "~/.drawing_search/index.json",
  "oda_converter_path": "/usr/bin/ODAFileConverter",
  "drawing_number_patterns": [],
  "scan_recursive": true,
  "log_level": "INFO"
}
```

---

## ライセンス

MIT License

---

## 依存ライブラリ

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| [PyMuPDF](https://pymupdf.readthedocs.io/) | PDF解析 | AGPL/commercial |
| [ezdxf](https://ezdxf.readthedocs.io/) | DXF/DWG解析 | MIT |
| [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) | DWG変換 | 無料配布 |
