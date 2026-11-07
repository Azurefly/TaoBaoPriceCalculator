'use strict';
// Add short entry points without replacing the existing guided UI or demo.
(() => {
 const target=document.querySelector('#screen-items .heading');
 if(!target)return;
 const row=document.createElement('div');row.className='actions';
 for(const [text,url,style] of [['从淘宝购物车导入','/shopping-tools#cart','primary'],['店铺与组合优惠','/shopping-tools#offers','']]){
   const button=document.createElement('button');button.textContent=text;button.className=style;
   button.addEventListener('click',()=>{location.href=url;});row.append(button);
 }
 target.append(row);
 const help=document.querySelector('.guidance .hint');
 if(help)help.textContent='可从首页连接购物车并补充店铺、组合优惠。当前页记录的仍应是淘宝实际应付金额。';
})();
