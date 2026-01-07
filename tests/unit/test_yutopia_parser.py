import unittest
from datetime import datetime
from parsers.yutopia_parser import YutopiaParser
from models.notice import Notice

class TestYutopiaParser(unittest.TestCase):
    def setUp(self):
        self.parser = YutopiaParser(
            list_selector="ul.columns-4 > li",
            title_selector="b.title",
            link_selector="a",
            content_selector="div.description div[data-role='wysiwyg-content']"
        )
        self.base_url = "https://yutopia.yu.ac.kr"
        self.site_key = "yutopia"

    def test_parse_list(self):
        html = """
        <ul class="columns-4">
            <li>
                <a href="/ko/program/all/view/20624?sort=date">
                    <div class="txt">
                        <b class="title">Y형 인재 육성 프로그램 [접수중] </b>
                        <div class="date">
                            <span class="state"><label class="OPEN">접수</label></span>
                            <small class="date_layer">
                                신청: <time datetime="2024-03-01">2024.03.01</time> ~ <time datetime="2024-03-31">2024.03.31</time>
                            </small>
                             <small class="date_layer">
                                운영: <time datetime="2024-04-01">2024.04.01</time> ~ <time datetime="2024-04-30">2024.04.30</time>
                            </small>
                        </div>
                    </div>
                </a>
            </li>
            <li>
                <a href="/ko/program/all/view/12345">
                    <b class="title">마감된 프로그램</b>
                    <span class="state"><label class="CLOSED">마감</label></span>
                </a>
            </li>
        </ul>
        """
        items = self.parser.parse_list(html, self.site_key, self.base_url)
        
        self.assertEqual(len(items), 2)
        
        # Item 1
        self.assertEqual(items[0].article_id, "20624")
        self.assertIn("Y형 인재 육성 프로그램", items[0].title)
        self.assertIn("[접수중]", items[0].title)
        self.assertEqual(items[0].url, "https://yutopia.yu.ac.kr/ko/program/all/view/20624?sort=date")
        self.assertEqual(items[0].extra_info.get("application_start"), "2024-03-01")
        self.assertEqual(items[0].extra_info.get("application_end"), "2024-03-31")
        
        # Item 2
        self.assertEqual(items[1].article_id, "12345")
        self.assertIn("[마감]", items[1].title)

    def test_parse_detail(self):
        html = """
        <div class="view">
            <div class="description">
                <div data-role="wysiwyg-content">
                    <p>상세 내용입니다.</p>
                    <img src="/images/poster.jpg" alt="poster">
                </div>
            </div>
            <div class="file-list">
                <a href="/attachment/download/999">안내문.pdf</a>
            </div>
            <div class="context-tab">
                <ul>
                    <li><a href="/ko/program/all/view/20624/notice">공지사항</a></li>
                </ul>
            </div>
        </div>
        """
        notice = Notice(
            site_key=self.site_key,
            article_id="20624",
            title="Temp",
            url="https://yutopia.yu.ac.kr/ko/program/all/view/20624"
        )
        
        notice = self.parser.parse_detail(html, notice)
        
        self.assertIn("상세 내용입니다.", notice.content)
        self.assertEqual(len(notice.attachments), 1)
        self.assertEqual(notice.attachments[0].name, "안내문.pdf")
        self.assertIn("https://yutopia.yu.ac.kr/attachment/download/999", notice.attachments[0].url)
        
        self.assertEqual(len(notice.image_urls), 1)
        self.assertIn("https://yutopia.yu.ac.kr/images/poster.jpg", notice.image_urls[0])
        
        self.assertIn("[공지사항 탭 바로가기]", notice.content)
        self.assertIn("/ko/program/all/view/20624/notice", notice.content)

if __name__ == "__main__":
    unittest.main()
