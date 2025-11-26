#!/usr/bin/env python
from __future__ import annotations

from typing import Generator, Optional
from urllib.parse import urljoin, urlparse

import htmlmin
from bs4 import BeautifulSoup
from pydantic import BaseModel, PrivateAttr


class WebPage(BaseModel):
    inner_text: str
    html: str
    url: str

    _soup: Optional[BeautifulSoup] = PrivateAttr(default=None)
    _title: Optional[str] = PrivateAttr(default=None)
    _icon: Optional[str] = PrivateAttr(default=None)

    @property
    def soup(self) -> BeautifulSoup:
        if self._soup is None:
            self._soup = BeautifulSoup(self.html, "html.parser")
        return self._soup

    @property
    def title(self):
        if self._title is None:
            title_tag = self.soup.find("title")
            self._title = title_tag.text.strip() if title_tag is not None else ""
        return self._title

    @property
    def icon(self):
        if self._icon is None:
            parsed_url = urlparse(self.url)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            rels = [
                "icon",
                "shortcut icon",
                "apple-touch-icon",
                "apple-touch-icon-precomposed"
            ]
            for rel in rels:
                icon_tag = self.soup.find(f"link[rel=\"{rel}\"]")
                if icon_tag is not None:
                    icon_href = icon_tag["href"]
                    if icon_href:
                        if icon_href.startswith("http://") or icon_href.startswith("https://"):
                            self._icon = icon_href
                        else:
                            self._icon = urljoin(base_url, icon_href)
                        break

            if self._icon is None:
                self._icon = urljoin(base_url, "favicon.ico")

        return self._icon


    def get_links(self) -> Generator[str, None, None]:
        for i in self.soup.find_all("a", href=True):
            url = i["href"]
            result = urlparse(url)
            if not result.scheme and result.path:
                yield urljoin(self.url, url)
            elif url.startswith(("http://", "https://")):
                yield urljoin(self.url, url)

    def get_slim_soup(self, keep_links: bool = False):
        soup = _get_soup(self.html)
        keep_attrs = ["class", "id"]
        if keep_links:
            keep_attrs.append("href")

        for i in soup.find_all(True):
            for name in list(i.attrs):
                if i[name] and name not in keep_attrs:
                    del i[name]

        for i in soup.find_all(["svg", "img", "video", "audio"]):
            i.decompose()

        return soup


def get_html_content(page: str, base: str):
    soup = _get_soup(page)

    return soup.get_text(strip=True)


def _get_soup(page: str):
    soup = BeautifulSoup(page, "html.parser")
    # https://stackoverflow.com/questions/1936466/how-to-scrape-only-visible-webpage-text-with-beautifulsoup
    for s in soup(["style", "script", "[document]", "head", "title", "footer"]):
        s.extract()

    return soup


def simplify_html(html: str, url: str, keep_links: bool = False):
    html = WebPage(inner_text="", html=html, url=url).get_slim_soup(keep_links).decode()
    return htmlmin.minify(html, remove_comments=True, remove_empty_space=True)
