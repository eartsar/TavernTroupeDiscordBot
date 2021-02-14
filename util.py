import re


class ValueRetainingRegexMatcher:
    '''This is a load of BS to just get around not using PEP 572'''
    def __init__(self, match_str):
        self.match_str = match_str


    def match(self, regex):
        self.retained = re.match(regex, self.match_str)
        return bool(self.retained)


    def search(self, regex):
        self.retained = re.search(regex, self.match_str)
        return bool(self.retained)


    def group(self, i):
        return self.retained.group(i)

