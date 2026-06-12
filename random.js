function getRandomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

let str = "";
for(i=0; i < 10; i++){
    const num = getRandomInt(0,1);
    str += num.toString();
};

console.log(str);

let newstr = "";
for (i = 0; i < str.length; i ++) {
    const winstr = str.substring(i , i + 2)
    console.log(winstr);
    if (winstr == "00" || winstr == "01") {
        newstr += "0";
    } else {
        newstr += "1";
    }
}

const strarr = str.split("");
const newarr = newstr.split("");
let numcorr = 0;
for (i = 0; i < strarr.length; i++) {
    if (newstr[i] == strarr[i]) {
        numcorr += 1;
    }
}

console.log(str);
console.log(newstr);
console.log(numcorr/strarr.length * 100);
