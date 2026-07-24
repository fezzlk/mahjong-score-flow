# mahjong-score-flow

麻雀の点数移動（精算）Webツール。

もともとTsumoAI（牌認識アプリ）の`/game`として実装されていた機能を独立させたもの。将来的にTsumoAIの牌認識APIを呼び出し、読み取った点数をこのツールにフィードバックする連携を予定している。

## Features

- ロン・ツモ・ダブロン・トリロン・流局の点数計算
- 箱割れ即終了、西入・北入などのルール設定
- ルームコード＋WebSocketによるリアルタイム対局同期（複数端末で同じ対局を見る）
- QRコードでの対局招待
- 席の交代・undo対応

## Run

```
docker compose up
```

`http://localhost:8000/game` で対局を開始できる。

## Test

```
pip install -r requirements.txt
pytest
```
