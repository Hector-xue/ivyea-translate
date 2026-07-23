# 主题素材来源与授权

六套图片主题的背景与精灵一律取自公有领域（Public domain）或 CC0，没有署名义务；
本文件仍逐张留档，便于核对与追溯。另有「清绿」「墨夜」两套纯色主题，不含任何图片素材。

绝大多数是实拍照片；「赛博」用的是一张 CC0 的数字绘画——霓虹赛博的调子实拍很难找到对味的。

程序对素材只做常规加工：等比缩放、居中裁切、按亮度/天空色抠像、调整饱和与亮度。
缩略图 `thumb.jpg` 由各主题的 `bg.jpg` 裁出；顶部横幅区域也是这张 `bg.jpg`
未被虚化的那一段（全窗共用一张图，不存在两张图相接），均不再单列。

| 主题 | 用途 | 原始文件 | 许可证 | 来源 |
|---|---|---|---|---|
| 常春藤 ivy | 背景底图 bg.jpg | Ivy-783084 640.jpg | CC0 | https://commons.wikimedia.org/wiki/File:Ivy-783084_640.jpg |
| 红旗 patriot | 旗面精灵 sprites/flag.png | Flag of the People's Republic of China.svg | Public domain | https://commons.wikimedia.org/wiki/File:Flag_of_the_People%27s_Republic_of_China.svg |
| 红旗 patriot | 背景底图 bg.jpg | Great Wall of China July 2006.JPG | CC0 | https://commons.wikimedia.org/wiki/File:Great_Wall_of_China_July_2006.JPG |
| 星海 starfield | 背景底图 bg.jpg | NASA Unveils Celestial Fireworks as Official Hubble 25th Anniversary Image.jpg | Public domain | https://commons.wikimedia.org/wiki/File:NASA_Unveils_Celestial_Fireworks_as_Official_Hubble_25th_Anniversary_Image.jpg |
| 樱花 sakura | 背景底图 bg.jpg | Prunus cerasoides phitsanulok Thailand พญาเสือโคร่ง พิษณุโลก.jpg | CC0 | https://commons.wikimedia.org/wiki/File:Prunus_cerasoides_phitsanulok_Thailand_%E0%B8%9E%E0%B8%8D%E0%B8%B2%E0%B9%80%E0%B8%AA%E0%B8%B7%E0%B8%AD%E0%B9%82%E0%B8%84%E0%B8%A3%E0%B9%88%E0%B8%87_%E0%B8%9E%E0%B8%B4%E0%B8%A9%E0%B8%93%E0%B8%B8%E0%B9%82%E0%B8%A5%E0%B8%81.jpg |
| 樱花 sakura | 花瓣精灵 sprites/petal_*.png | Cherry Blossom.jpg | CC0 | https://commons.wikimedia.org/wiki/File:Cherry_Blossom.jpg |
| 赛博 cyber | 霓虹精灵 sprites/neon.png | Neon Dragon at Museum of Neon Art.jpg | CC0 | https://commons.wikimedia.org/wiki/File:Neon_Dragon_at_Museum_of_Neon_Art.jpg |
| 赛博 cyber | 背景底图 bg.jpg | Cyberpunk corridor.jpg | CC0 | https://commons.wikimedia.org/wiki/File:Cyberpunk_corridor.jpg |
| 雪山 alpine | 云雾精灵 sprites/fog.png | Fog Rolls Over Netstal (Unsplash).jpg | CC0 | https://commons.wikimedia.org/wiki/File:Fog_Rolls_Over_Netstal_(Unsplash).jpg |
| 雪山 alpine | 背景底图 bg.jpg | Mount Everest from Rongbuk may 2005.JPG | Public domain | https://commons.wikimedia.org/wiki/File:Mount_Everest_from_Rongbuk_may_2005.JPG |

## 另有说明

- 常春藤的叶片精灵 `themes/ivy/sprites/leaf_s*.png` 复用自本仓库门户站 `site/assets/leaves/`，
  原图为 Wikimedia Commons 上 Andrikkos 拍摄的常春藤扫描件（公有领域）。
- 红旗主题横幅里飘的旗面 `sprites/flag.png`，由 Commons 的
  `Flag of the People's Republic of China.svg`（官方旗面设计，公有领域）
  经服务端渲染为位图后缩放而来；飘动是程序按正弦位移 + 明暗着色算出来的。
- 素材经 Wikimedia Commons API 于 2026-07-22/23 获取，许可证字段取自各文件页的 `LicenseShortName`。
