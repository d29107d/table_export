return {		--[[ 04_edge_cases.xlsx -> Edge ]]
[1] = {
	id = 1,
	multiline = [[第一行
第二行]],
	square_brackets = [=[a]]b]=],
	trailing_bracket = [=[[边界]]=],
	optional_text = [[有值]],
	blank_number = 100,
	blank_table = {{1, 2}},
	flag = true,
},
[2] = {
	id = 2,
	multiline = [[单行文本]],
	square_brackets = [[a]b]],
	trailing_bracket = [=[尾]]=],
	optional_text = [[ ]],
	blank_number = nil,
	blank_table = {},
	flag = false,
},
[3] = {
	id = 3,
	square_brackets = [[普通]],
	trailing_bracket = [[正常]],
	flag = nil,
},
}