"""Scientific illustration of native audit output, not calculation geometry."""
import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    native = json.loads(args.native.read_bytes())
    review = json.loads(args.review.read_bytes())
    image = Image.new('RGB', (1600, 1250), '#f5f7fa')
    draw = ImageDraw.Draw(image)
    font_path = '/System/Library/Fonts/Supplemental/Arial.ttf'
    font = lambda n: ImageFont.truetype(font_path, n)
    draw.text((40, 24), 'Кустанайская — проверка составных контуров', fill='#172333', font=font(34))
    draw.text((40, 72), 'Площади вычислены AutoCAD — исходный чертёж не изменён', fill='#536176', font=font(23))
    colors = {'wall': '#245de8', 'boundary': '#c57410', 'connector': '#c93043'}
    for x, label, color in [(40, 'Линии здания', colors['wall']), (380, 'Фрагмент чужой границы', colors['boundary']), (895, 'Добавленное соединение', colors['connector'])]:
        draw.line((x, 126, x+42, 126), fill=color, width=5)
        draw.text((x+55, 110), label, fill='#344359', font=font(22))

    panels = [
        ('6E16/1095', 'Составная цепочка и граница заказа', 'Пять исходных линий и соединение 7,97 мм'),
        ('6E16/78DC', 'Замыкание фрагментом чужой границы', 'Четыре исходные линии без добавления отрезков'),
        ('6E16/AE50', 'Замкнутый контур с выступающим хвостом', 'Площадь найдена без замыкания концов'),
        (None, 'Один из трёх повторяющихся знаков', 'Три линии одного блока — не контур площади'),
    ]
    for panel, (target, title, caption) in enumerate(panels):
        x = 40+(panel % 2)*780
        y = 165+(panel // 2)*520
        draw.rounded_rectangle((x, y, x+740, y+490), radius=16, fill='white', outline='#dce3ed', width=2)
        draw.text((x+24, y+20), title, fill='#172333', font=font(24))
        face = None
        if target:
            candidates = [f for f in native['faces'] if f.get('area', 0) > 20 and any(
                target in native['pieces'][i].get('source_routes', [native['pieces'][i]['route']])
                for i in f['pieces'])]
            # Select this illustration's local face, not an automatic semantic decision.
            face = min(candidates, key=lambda f: f['area'])
            segments = [native['pieces'][i] for i in face['pieces']]
            label = f"{face['area']:,.2f} м²".replace(',', ' ').replace('.', ',')
        else:
            segments = [{'route': item['route'], 'display_points': item['path']}
                        for item in review['items'] if item['route'].startswith('6E16/457A/')]
            label = '457A  /  457D + 457E + 457F'
        all_points = [p for s in segments for p in s['display_points']]
        xmin, xmax = min(p[0] for p in all_points), max(p[0] for p in all_points)
        ymin, ymax = min(p[1] for p in all_points), max(p[1] for p in all_points)
        scale = min(640/max(xmax-xmin, .001), 330/max(ymax-ymin, .001))
        cx, cy = (xmin+xmax)/2, (ymin+ymax)/2
        project = lambda p: (x+370+(p[0]-cx)*scale, y+245-(p[1]-cy)*scale)
        if face:
            # Orient native display segments in the face-walk order for filling.
            paths = [s['display_points'] for s in segments]
            first = paths[0]
            if len(paths) > 1:
                def connects(p, path):
                    return min(math.dist(p[:2], path[0][:2]), math.dist(p[:2], path[-1][:2])) < 1e-6
                if not connects(first[-1], paths[1]):
                    first = list(reversed(first))
            points = list(first)
            for path in paths[1:]:
                if math.dist(points[-1][:2], path[-1][:2]) < math.dist(points[-1][:2], path[0][:2]):
                    path = list(reversed(path))
                points.extend(path)
            draw.polygon([project(p) for p in points], fill='#d6eee4')
        for segment in segments:
            route = segment['route']
            color = colors['connector'] if route.startswith('connector:') else colors['boundary'] if route == '6E16/16020' else colors['wall']
            points = [project(p) for p in segment['display_points']]
            draw.line(points, fill=color, width=4, joint='curve')
            if route.startswith('connector:'):
                px, py = points[0]
                draw.ellipse((px-8, py-8, px+8, py+8), outline=color, width=3)
        if target == '6E16/AE50':
            original = next(t['path'] for t in review['items'] if t['route'] == target)
            draw.line([project(p) for p in original[-2:]], fill=colors['connector'], width=5)
            px, py = project(original[-1])
            draw.ellipse((px-10, py-10, px+10, py+10), outline=colors['connector'], width=3)
        draw.text((x+24, y+420), label, fill='#176349' if face else '#344359', font=font(26))
        draw.text((x+24, y+458), caption, fill='#536176', font=font(19))
    draw.text((40, 1216), 'Диагностические кандидаты областей  |  Не массовое подтверждение зданий в проекте', fill='#536176', font=font(19))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print(args.output)


if __name__ == '__main__':
    main()
