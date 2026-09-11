return {		--[[ 04_edge_cases.xlsx -> Edge ]]
[1] = {
	id = 1,
	multiline = [[line one
line two]],
	square_brackets = [=[a]]b]=],
	trailing_bracket = [=[[edge]]=],
	optional_text = [[has value]],
	blank_number = 100,
	blank_table = {{1, 2}},
	flag = true,
},
[2] = {
	id = 2,
	multiline = [[single line]],
	square_brackets = [[a]b]],
	trailing_bracket = [=[tail]]=],
	optional_text = [[ ]],
	blank_number = nil,
	blank_table = {},
	flag = false,
},
[3] = {
	id = 3,
	square_brackets = [[plain]],
	trailing_bracket = [[normal]],
	flag = nil,
},
}